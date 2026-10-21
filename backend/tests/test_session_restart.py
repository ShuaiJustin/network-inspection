"""A restart wipes the in-memory sessions, so a stored token must not be trusted."""

from fastapi.testclient import TestClient

from app.models.device import Base
from app.db import engine
from app.routers import auth as auth_router
from main import app


client = TestClient(app)


def setup_function():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    auth_router.SESSION_TOKENS.clear()


def _login() -> str:
    client.post("/auth/bootstrap", json={"username": "admin", "password": "inspect123"})
    response = client.post("/auth/login", json={"username": "admin", "password": "inspect123"})
    return response.json()["access_token"]


def test_a_live_session_can_be_verified_without_touching_business_data():
    token = _login()

    response = client.get("/auth/session", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 204


def test_a_token_left_over_from_a_previous_run_is_rejected():
    token = _login()
    # Restarting the sidecar drops every in-memory session while the browser
    # keeps the token in localStorage.
    auth_router.SESSION_TOKENS.clear()

    response = client.get("/auth/session", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401


def test_verifying_without_any_token_is_rejected():
    _login()

    response = client.get("/auth/session")

    assert response.status_code == 401
