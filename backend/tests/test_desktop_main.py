import desktop_main
import pytest


class _FakeKernel32:
    def __init__(self, exit_code: int):
        self.exit_code = exit_code
        self.closed_handles: list[int] = []

    def OpenProcess(self, _access: int, _inherit_handle: bool, _process_id: int) -> int:
        return 99

    def GetExitCodeProcess(self, _handle: int, exit_code) -> int:
        exit_code._obj.value = self.exit_code
        return 1

    def CloseHandle(self, handle: int) -> int:
        self.closed_handles.append(handle)
        return 1


def test_desktop_main_accepts_local_host_and_port_arguments():
    args = desktop_main.parse_args(["--host", "127.0.0.1", "--port", "18000"])

    assert args.host == "127.0.0.1"
    assert args.port == 18000


def test_desktop_main_accepts_tauri_parent_process_argument():
    args = desktop_main.parse_args(["--parent-pid", "12345"])

    assert args.parent_pid == 12345


def test_parent_process_checker_returns_false_when_process_has_exited(monkeypatch):
    kernel32 = _FakeKernel32(exit_code=0)
    monkeypatch.setattr(desktop_main.ctypes, "windll", type("WinDll", (), {"kernel32": kernel32})())

    assert desktop_main._parent_process_is_running(12345) is False
    assert kernel32.closed_handles == [99]


def test_startup_log_path_uses_configured_data_directory(monkeypatch, tmp_path):
    monkeypatch.setenv("NETWORK_INSPECTION_DATA_DIR", str(tmp_path))

    assert desktop_main._startup_log_path() == tmp_path / "logs" / "sidecar-startup.log"


def test_startup_log_is_rotated_at_the_size_limit(tmp_path):
    log_path = tmp_path / "logs" / "sidecar-startup.log"
    log_path.parent.mkdir()
    log_path.write_bytes(b"x" * (desktop_main.STARTUP_LOG_MAX_BYTES + 1))

    desktop_main._rotate_startup_log(log_path)

    assert not log_path.exists()
    assert log_path.with_suffix(".log.1").stat().st_size == desktop_main.STARTUP_LOG_MAX_BYTES + 1


def test_run_logs_an_application_import_failure(monkeypatch, tmp_path):
    monkeypatch.setenv("NETWORK_INSPECTION_DATA_DIR", str(tmp_path))

    def fail_to_load_server():
        raise RuntimeError("database migration failed")

    monkeypatch.setattr(desktop_main, "_load_server", fail_to_load_server)

    with pytest.raises(RuntimeError, match="database migration failed"):
        desktop_main.run([])

    log_text = (tmp_path / "logs" / "sidecar-startup.log").read_text(encoding="utf-8")
    assert "Traceback" in log_text
    assert "RuntimeError: database migration failed" in log_text
