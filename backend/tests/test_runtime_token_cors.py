"""The desktop UI runs on a different origin than the local sidecar, so every
response it must read - including the rejections - has to carry CORS headers.
"""

import hashlib
import hmac
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import main


TAURI_ORIGIN = "http://tauri.localhost"
PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def guarded_client():
    previous = getattr(main.app.state, "runtime_token", None)
    main.configure_runtime_token("unit-test-token")
    with TestClient(main.app) as client:
        yield client
    main.configure_runtime_token(previous)


def test_rejected_request_is_still_readable_by_the_browser(guarded_client):
    response = guarded_client.get("/auth/status", headers={"Origin": TAURI_ORIGIN})

    assert response.status_code == 401
    # Without this header the browser discards the response and the UI only ever
    # sees an opaque "Failed to fetch" instead of the real reason.
    assert response.headers.get("access-control-allow-origin") == TAURI_ORIGIN


def test_wrong_token_rejection_is_also_readable(guarded_client):
    response = guarded_client.get(
        "/auth/status",
        headers={"Origin": TAURI_ORIGIN, "X-Inspection-Run-Token": "wrong"},
    )

    assert response.status_code == 401
    assert response.headers.get("access-control-allow-origin") == TAURI_ORIGIN


def test_accepted_request_keeps_its_cors_headers(guarded_client):
    response = guarded_client.get(
        "/auth/status",
        headers={"Origin": TAURI_ORIGIN, "X-Inspection-Run-Token": "unit-test-token"},
    )

    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == TAURI_ORIGIN


def test_an_unknown_origin_is_still_refused_cors(guarded_client):
    response = guarded_client.get(
        "/auth/status",
        headers={"Origin": "http://evil.example", "X-Inspection-Run-Token": "unit-test-token"},
    )

    assert "access-control-allow-origin" not in response.headers


def test_health_uses_a_nonce_hmac_without_sending_the_runtime_token(guarded_client):
    nonce_bytes = bytes(range(32))
    response = guarded_client.get(
        f"/health?nonce={nonce_bytes.hex()}",
        headers={"Origin": TAURI_ORIGIN},
    )

    expected_proof = hmac.new(
        b"unit-test-token", nonce_bytes, hashlib.sha256
    ).hexdigest()
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "proof": expected_proof}
    assert "unit-test-token" not in response.text
    assert response.headers.get("access-control-allow-origin") == TAURI_ORIGIN


@pytest.mark.parametrize("nonce", [None, "00", "z" * 64, "00" * 33])
def test_guarded_health_without_a_valid_nonce_remains_token_protected(
    guarded_client, nonce
):
    path = "/health" if nonce is None else f"/health?nonce={nonce}"

    response = guarded_client.get(path)

    assert response.status_code == 401
    assert "proof" not in response.json()


@pytest.mark.parametrize("nonce", ["00", "z" * 64, "00" * 33])
def test_guarded_health_validates_nonce_when_token_is_provided(guarded_client, nonce):
    response = guarded_client.get(
        f"/health?nonce={nonce}",
        headers={"X-Inspection-Run-Token": "unit-test-token"},
    )

    assert response.status_code == 400
    assert "proof" not in response.json()


def test_plain_health_keeps_its_token_authenticated_response(guarded_client):
    response = guarded_client.get(
        "/health", headers={"X-Inspection-Run-Token": "unit-test-token"}
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_proof_changes_with_the_nonce(guarded_client):
    first = guarded_client.get(f"/health?nonce={(bytes([1]) * 32).hex()}")
    second = guarded_client.get(f"/health?nonce={(bytes([2]) * 32).hex()}")

    assert first.status_code == second.status_code == 200
    assert first.json()["proof"] != second.json()["proof"]


def test_health_without_a_runtime_token_keeps_the_development_response():
    previous = getattr(main.app.state, "runtime_token", None)
    main.configure_runtime_token(None)
    try:
        with TestClient(main.app) as client:
            response = client.get("/health")
    finally:
        main.configure_runtime_token(previous)

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_development_frontend_receives_the_same_runtime_token_as_backend():
    script = (PROJECT_ROOT / "start-dev.ps1").read_text(encoding="utf-8-sig")
    api_client = (PROJECT_ROOT / "frontend" / "src" / "app" / "api-client.ts").read_text(
        encoding="utf-8"
    )

    backend_start = script.index("$backendProcess = Start-BackendProcess")
    frontend_start = script.index("$frontendProcess = Start-FrontendProcess")
    token_clear = script.index("$runtimeToken = $null", backend_start)

    assert backend_start < frontend_start < token_clear
    assert '$startInfo.EnvironmentVariables["INSPECTION_RUNTIME_TOKEN"] = $RuntimeToken' in script
    assert '$startInfo.EnvironmentVariables["VITE_INSPECTION_RUNTIME_TOKEN"] = $RuntimeToken' in script
    assert "import.meta.env.VITE_INSPECTION_RUNTIME_TOKEN" in api_client
