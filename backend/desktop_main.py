import argparse
import ctypes
from datetime import datetime
import os
from pathlib import Path
import sys
import threading
import time
import traceback


STARTUP_LOG_MAX_BYTES = 1024 * 1024


def _application_data_dir() -> Path:
    if configured_data_dir := os.environ.get("NETWORK_INSPECTION_DATA_DIR"):
        return Path(configured_data_dir)
    if getattr(sys, "frozen", False):
        return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "NetworkInspection"
    return Path(__file__).resolve().parent / "data"


def _startup_log_path() -> Path:
    return _application_data_dir() / "logs" / "sidecar-startup.log"


def _rotate_startup_log(log_path: Path) -> None:
    try:
        if not log_path.is_file() or log_path.stat().st_size < STARTUP_LOG_MAX_BYTES:
            return
        backup_path = log_path.with_suffix(f"{log_path.suffix}.1")
        backup_path.unlink(missing_ok=True)
        log_path.replace(backup_path)
    except OSError:
        pass


def _ensure_standard_streams(log_path: Path | None = None) -> None:
    """Give the process real stdout/stderr objects.

    The sidecar is built with PyInstaller's --noconsole so Windows does not
    attach a console window to it. Without a console those streams are None,
    and any library that writes to them (uvicorn's logging setup is the first)
    raises instead of starting the server.
    """
    missing_streams = [name for name in ("stdout", "stderr") if getattr(sys, name, None) is None]
    if not missing_streams:
        return
    try:
        if log_path:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            stream = log_path.open("a", encoding="utf-8", buffering=1)
        else:
            stream = open(os.devnull, "w", encoding="utf-8")
    except OSError:
        stream = open(os.devnull, "w", encoding="utf-8")
    for name in missing_streams:
        setattr(sys, name, stream)


def _write_startup_failure(log_path: Path, error: BaseException) -> None:
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as stream:
            stream.write(f"\n[{datetime.now().isoformat(timespec='seconds')}] Sidecar startup failed\n")
            traceback.print_exception(type(error), error, error.__traceback__, file=stream)
    except OSError:
        try:
            traceback.print_exception(type(error), error, error.__traceback__, file=sys.stderr)
        except Exception:
            pass


def _load_server():
    import uvicorn

    from main import app, configure_runtime_token

    return uvicorn, app, configure_runtime_token


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8000, type=int)
    parser.add_argument("--parent-pid", type=int)
    return parser.parse_args(argv)


def _parent_process_is_running(parent_pid: int) -> bool:
    if parent_pid <= 0:
        return False
    kernel32 = ctypes.windll.kernel32
    process = kernel32.OpenProcess(0x1000, False, parent_pid)
    if not process:
        return False
    exit_code = ctypes.c_ulong()
    try:
        if not kernel32.GetExitCodeProcess(process, ctypes.byref(exit_code)):
            return False
        return exit_code.value == 259  # STILL_ACTIVE
    finally:
        kernel32.CloseHandle(process)


def _exit_when_parent_stops(parent_pid: int) -> None:
    while _parent_process_is_running(parent_pid):
        time.sleep(1)
    os._exit(0)


def run(argv: list[str] | None = None) -> None:
    log_path = _startup_log_path()
    _rotate_startup_log(log_path)
    _ensure_standard_streams(log_path)
    try:
        args = parse_args(argv)
        uvicorn, app, configure_runtime_token = _load_server()
        configure_runtime_token(os.environ.get("INSPECTION_RUNTIME_TOKEN"))
        if args.parent_pid:
            threading.Thread(
                target=_exit_when_parent_stops,
                args=(args.parent_pid,),
                daemon=True,
            ).start()
        # Keep uvicorn from installing console stream handlers in a windowless process.
        uvicorn.run(app, host=args.host, port=args.port, log_level="warning", log_config=None)
    except Exception as error:
        _write_startup_failure(log_path, error)
        raise


if __name__ == "__main__":
    run()
