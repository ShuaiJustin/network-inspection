import struct
import os
from pathlib import Path

import pytest

BINARIES = Path(__file__).resolve().parents[2] / "src-tauri" / "binaries"
IMAGE_SUBSYSTEM_WINDOWS_GUI = 2
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _pe_subsystem(path: Path) -> int:
    header = path.read_bytes()[:0x400]
    pe_offset = struct.unpack_from("<I", header, 0x3C)[0]
    assert header[pe_offset:pe_offset + 4] == b"PE\0\0", f"{path.name} is not a PE image"
    # Subsystem sits 68 bytes into the optional header, which follows the
    # 24-byte COFF header.
    return struct.unpack_from("<H", header, pe_offset + 24 + 68)[0]


@pytest.mark.parametrize(
    "name",
    [
        "inspection-api-x86_64-pc-windows-msvc.exe",
        "inspection-api-x86_64-pc-windows-gnu.exe",
    ],
)
def test_sidecar_is_built_without_a_console(name: str):
    """The sidecar must not be a console application.

    Windows gives every CONSOLE-subsystem process a console window, so a
    PyInstaller build without --noconsole makes a black terminal flash up (and
    stay up) next to the desktop window every time the app starts.
    """
    binary = BINARIES / name
    if not binary.exists():
        pytest.skip(f"{name} has not been built yet")

    assert _pe_subsystem(binary) == IMAGE_SUBSYSTEM_WINDOWS_GUI, (
        f"{name} is a console application; rebuild the sidecar with --noconsole"
    )


def test_build_script_requests_a_windowless_sidecar():
    script = (Path(__file__).resolve().parents[1] / "build-sidecar.ps1").read_text(encoding="utf-8")
    assert "--noconsole" in script, "build-sidecar.ps1 must pass --noconsole to PyInstaller"


def test_tauri_release_entry_point_uses_the_windows_gui_subsystem():
    source = (PROJECT_ROOT / "src-tauri" / "src" / "main.rs").read_text(encoding="utf-8")
    assert '#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]' in source, (
        "the Tauri release executable must use the Windows GUI subsystem"
    )


def test_tauri_desktop_uses_single_instance_before_starting_the_sidecar():
    source = (PROJECT_ROOT / "src-tauri" / "src" / "main.rs").read_text(encoding="utf-8")
    manifest = (PROJECT_ROOT / "src-tauri" / "Cargo.toml").read_text(encoding="utf-8")

    assert 'tauri-plugin-single-instance = "2"' in manifest
    assert "tauri_plugin_single_instance::init" in source
    assert source.index("tauri_plugin_single_instance::init") < source.index("tauri_plugin_shell::init")
    assert 'get_webview_window("main")' in source
    assert "window.show()" in source
    assert "window.set_focus()" in source


def test_built_tauri_release_does_not_open_a_console():
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        pytest.skip("LOCALAPPDATA is unavailable")
    binary = (
        Path(local_app_data)
        / "NetworkInspectionBuild"
        / "cargo-target"
        / "x86_64-pc-windows-gnu"
        / "release"
        / "network-inspection.exe"
    )
    if not binary.exists():
        pytest.skip("the Tauri release executable has not been built yet")

    assert _pe_subsystem(binary) == IMAGE_SUBSYSTEM_WINDOWS_GUI, (
        "network-inspection.exe is a console application; rebuild it after adding "
        "windows_subsystem = \"windows\""
    )


def test_installer_build_rejects_console_subsystem_executables():
    script = (PROJECT_ROOT / "build-windows-installer.ps1").read_text(encoding="utf-8-sig")
    assert "function Assert-WindowsGuiSubsystem" in script
    assert 'Assert-WindowsGuiSubsystem (Join-Path $releaseDirectory "network-inspection.exe")' in script
    assert 'Assert-WindowsGuiSubsystem (Join-Path $releaseDirectory "inspection-api.exe")' in script


def test_standard_streams_are_restored_when_the_process_has_no_console(monkeypatch):
    """--noconsole leaves sys.stdout/stderr as None.

    uvicorn's default logging config then fails with "Unable to configure
    formatter", so the API never binds its port and the window sits forever on
    "??????????". Verified by running uvicorn with both streams set to
    None: it raised ValueError before this guard was added.
    """
    import desktop_main

    monkeypatch.setattr(desktop_main.sys, "stdout", None)
    monkeypatch.setattr(desktop_main.sys, "stderr", None)

    desktop_main._ensure_standard_streams()

    assert desktop_main.sys.stdout is not None
    assert desktop_main.sys.stderr is not None
    desktop_main.sys.stdout.write("no console here")
    desktop_main.sys.stderr.write("no console here")


def test_server_start_does_not_rely_on_uvicorn_stream_logging():
    source = (Path(__file__).resolve().parents[1] / "desktop_main.py").read_text(encoding="utf-8")
    # Assert on the call itself, not on the file text: the surrounding comment
    # also mentions log_config=None and would mask a regression in the call.
    call = next(line for line in source.splitlines() if "uvicorn.run(" in line)
    assert "log_config=None" in call, (
        "uvicorn must not install its stream log handlers in a windowless process"
    )


def test_entry_point_installs_the_stream_guard_before_serving():
    """The guard has to run in __main__, not just exist as a function.

    Deleting the call site leaves every other assertion in this module green
    while the packaged sidecar still dies on startup.
    """
    source = (Path(__file__).resolve().parents[1] / "desktop_main.py").read_text(encoding="utf-8")
    runner = source[source.index("def run("):source.index('if __name__ == "__main__":')]
    assert runner.index("_ensure_standard_streams(") < runner.index("_load_server(")

    before_loader = source[:source.index("def _load_server(")]
    assert "from main import" not in before_loader, (
        "the application must not be imported before startup logging is available"
    )
