import json
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = PROJECT_ROOT / "backend"
TAURI_ROOT = PROJECT_ROOT / "src-tauri"

EXPECTED_RUNTIME_REQUIREMENTS = {
    "argon2-cffi==25.1.0",
    "fastapi==0.135.3",
    "netmiko==4.7.0",
    "openpyxl==3.1.2",
    "paramiko==4.0.0",
    "pydantic==2.12.5",
    "pysnmp==7.1.28",
    "python-multipart==0.0.32",
    "sqlalchemy==2.0.49",
    "starlette==1.3.1",
    "uvicorn[standard]==0.44.0",
}


def _requirement_lines(filename: str) -> set[str]:
    return {
        line
        for raw_line in (BACKEND_ROOT / filename).read_text(encoding="utf-8").splitlines()
        if (line := raw_line.strip()) and not line.startswith("#")
    }


def test_python_requirements_are_pinned_and_separated_by_purpose() -> None:
    assert _requirement_lines("requirements-runtime.txt") == EXPECTED_RUNTIME_REQUIREMENTS
    assert _requirement_lines("requirements-build.txt") == {
        "-r requirements-runtime.txt",
        "pyinstaller==6.20.0",
    }
    assert _requirement_lines("requirements-dev.txt") == {
        "-r requirements-runtime.txt",
        "httpx==0.28.1",
        "pytest==9.0.3",
    }
    assert _requirement_lines("requirements.txt") == {"-r requirements-dev.txt"}


def test_sidecar_build_uses_only_an_isolated_build_environment() -> None:
    script = (BACKEND_ROOT / "build-sidecar.ps1").read_text(encoding="utf-8")

    assert '$venv = Join-Path $work "venv"' in script
    assert "-m venv --clear $venv" in script
    assert '$venvPython = Join-Path $venv "Scripts\\python.exe"' in script
    assert '$buildRequirements = Join-Path $root "requirements-build.txt"' in script
    assert "& $venvPython -m pip install" in script
    assert "-r $buildRequirements" in script
    assert "& $venvPython -m PyInstaller" in script
    assert "--system-site-packages" not in script


def test_installer_reinstalls_frontend_and_locks_the_cargo_build() -> None:
    script = (PROJECT_ROOT / "build-windows-installer.ps1").read_text(
        encoding="utf-8-sig"
    )

    assert "& npm.cmd ci" in script
    assert script.index("& npm.cmd ci") < script.index("& npm.cmd run build")
    assert '$env:RUSTUP_TOOLCHAIN = "stable-x86_64-pc-windows-gnu"' in script
    tauri_build = next(line for line in script.splitlines() if "tauri.cmd" in line and " build " in line)
    assert "--target x86_64-pc-windows-gnu" in tauri_build
    assert "-- --locked" in tauri_build
    assert "BuildRoot -notmatch" in script
    assert "BuildRoot must contain ASCII characters" in script


def test_tauri_base_configuration_is_branded_and_consistent() -> None:
    config = json.loads((TAURI_ROOT / "tauri.conf.json").read_text(encoding="utf-8"))
    cargo_manifest = tomllib.loads((TAURI_ROOT / "Cargo.toml").read_text(encoding="utf-8"))
    cargo_lock = tomllib.loads((TAURI_ROOT / "Cargo.lock").read_text(encoding="utf-8"))
    locked_app = next(
        package for package in cargo_lock["package"] if package["name"] == "network-inspection"
    )

    assert config["productName"] == "\u7f51\u7edc\u8bbe\u5907\u5de1\u68c0"
    assert config["identifier"] == "com.local.networkinspection"
    assert config["version"] == cargo_manifest["package"]["version"] == locked_app["version"]
    assert config["app"]["windows"][0]["title"] == config["productName"]
    assert config["bundle"]["icon"] == ["icons/icon.ico", "icons/icon.png"]
    assert all((TAURI_ROOT / icon).is_file() for icon in config["bundle"]["icon"])
    assert not (PROJECT_ROOT / "tauri.json").exists()
    assert not (TAURI_ROOT / "tauri.json").exists()


def test_tauri_paints_the_startup_screen_before_waiting_for_the_sidecar() -> None:
    config = json.loads((TAURI_ROOT / "tauri.conf.json").read_text(encoding="utf-8"))
    window = config["app"]["windows"][0]
    main_source = (TAURI_ROOT / "src" / "main.rs").read_text(encoding="utf-8")
    setup = main_source[main_source.index(".setup(|app|") : main_source.index(".on_window_event")]

    assert window["backgroundColor"] == "#f4f6f8"
    assert setup.index("app.manage(BackendState") < setup.index("tauri::async_runtime::spawn")
    assert "restart_backend_blocking" in setup
    assert "start_and_activate_backend(app.handle()" not in setup


def test_tauri_renderer_capability_stays_minimal() -> None:
    capability = json.loads(
        (TAURI_ROOT / "capabilities" / "default.json").read_text(encoding="utf-8")
    )

    assert capability["windows"] == ["main"]
    assert capability["permissions"] == [
        "core:default",
        "dialog:allow-save",
        "fs:allow-write-file",
    ]


@pytest.mark.parametrize(
    "relative_path",
    ["backend/build-sidecar.ps1", "build-windows-installer.ps1"],
)
def test_build_powershell_scripts_parse(relative_path: str) -> None:
    powershell = shutil.which("powershell.exe")
    if not powershell:
        pytest.skip("Windows PowerShell is unavailable")
    path = (PROJECT_ROOT / relative_path).resolve()
    escaped_path = str(path).replace("'", "''")
    command = (
        "$tokens = $null; $errors = $null; "
        f"[void][System.Management.Automation.Language.Parser]::ParseFile('{escaped_path}', "
        "[ref]$tokens, [ref]$errors); "
        "if ($errors.Count) { $errors | ForEach-Object { Write-Error $_ }; exit 1 }"
    )

    completed = subprocess.run(
        [powershell, "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
