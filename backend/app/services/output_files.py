import errno
import re
import shutil
from contextlib import contextmanager
from uuid import uuid4
from pathlib import Path

from app.db import DATA_DIR
from app.models.device import AppSettings


def report_directory(settings: AppSettings) -> Path:
    directory = Path(settings.report_directory) if settings.report_directory else DATA_DIR / "reports"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def safe_filename_part(value: str) -> str:
    value = re.sub(r'[\\/:*?"<>|]+', "_", value)
    return re.sub(r"\s+", "_", value).strip(" ._") or "inspection"


def unique_file_path(directory: Path, filename: str) -> Path:
    candidate = directory / filename
    suffix = 2
    while candidate.exists():
        candidate = directory / f"{Path(filename).stem}_{suffix}{Path(filename).suffix}"
        suffix += 1
    return candidate


@contextmanager
def reserved_unique_file_path(directory: Path, filename: str):
    """Atomically claim a filename and remove the claim if writing fails."""
    candidate = directory / filename
    suffix = 2
    while True:
        try:
            candidate.touch(exist_ok=False)
            break
        except FileExistsError:
            candidate = directory / f"{Path(filename).stem}_{suffix}{Path(filename).suffix}"
            suffix += 1
    try:
        yield candidate
    except BaseException:
        try:
            candidate.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def ensure_available_space(directory: Path, required_bytes: int) -> None:
    if shutil.disk_usage(directory).free < max(0, required_bytes):
        raise OSError(errno.ENOSPC, "Insufficient disk space")


def is_writable_directory(value: str) -> bool:
    directory = Path(value)
    if not directory.is_dir():
        return False
    probe = directory / f".network-inspection-{uuid4().hex}.tmp"
    try:
        probe.touch(exist_ok=False)
        probe.unlink()
    except OSError:
        try:
            probe.unlink(missing_ok=True)
        except OSError:
            pass
        return False
    return True
