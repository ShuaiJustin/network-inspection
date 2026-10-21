from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import DATA_DIR
from app.models.device import ActivityLog, InspectionResult, InspectionTask
from app.services.security import redact_configuration_text


MAX_TASK_RAW_OUTPUT_BYTES = 200 * 1024 * 1024
RAW_OUTPUT_DIR = DATA_DIR / "raw_outputs"


@dataclass(frozen=True)
class StoredRawOutput:
    file_path: str
    status: str
    received_bytes: int
    stored_bytes: int


def _safe_path_part(value: str) -> str:
    return "".join(character if character.isalnum() or character in {"-", "_", "."} else "_" for character in value)


def store_raw_output(
    directory: Path,
    *,
    task_id: int,
    device_ip: str,
    content: str,
    existing_task_bytes: int,
    max_task_bytes: int = MAX_TASK_RAW_OUTPUT_BYTES,
) -> StoredRawOutput:
    encoded = content.encode("utf-8")
    remaining_bytes = max(0, max_task_bytes - existing_task_bytes)
    retained = encoded[:remaining_bytes].decode("utf-8", errors="ignore").encode("utf-8")
    status = "complete" if len(retained) == len(encoded) else "truncated"
    target_directory = directory / f"task_{task_id}"
    target_directory.mkdir(parents=True, exist_ok=True)
    target_path = target_directory / f"{_safe_path_part(device_ip)}_{uuid4().hex}.txt"
    temporary_path = target_path.with_name(f".{target_path.name}.tmp")
    try:
        temporary_path.write_bytes(retained)
        temporary_path.replace(target_path)
    finally:
        temporary_path.unlink(missing_ok=True)
    return StoredRawOutput(
        file_path=str(target_path),
        status=status,
        received_bytes=len(encoded),
        stored_bytes=len(retained),
    )


def persist_inspection_result_raw_output(session: Session, result: InspectionResult) -> None:
    if not result.raw_output:
        return
    existing_task_bytes = session.query(
        func.coalesce(func.sum(InspectionResult.raw_output_bytes), 0)
    ).filter(
        InspectionResult.task_id == result.task_id,
        InspectionResult.id != result.id,
    ).scalar() or 0
    stored = store_raw_output(
        RAW_OUTPUT_DIR,
        task_id=result.task_id,
        device_ip=result.device_ip,
        content=redact_configuration_text(result.raw_output),
        existing_task_bytes=int(existing_task_bytes),
    )
    result.raw_output = None
    result.raw_output_ref = stored.file_path
    result.raw_output_status = "truncated" if (
        result.raw_output_status == "truncated" or stored.status == "truncated"
    ) else "complete"
    result.raw_output_bytes = max(result.raw_output_bytes or 0, stored.received_bytes)
    if result.raw_output_status == "truncated":
        result.conclusion = "data_fetch_failed"
        result.collection_status = "failed"
        result.failure_stage = "output_truncated"
        message = "Raw output reached its storage limit"
        result.message = f"{result.message}; {message}" if result.message else message


def read_raw_output(file_path: str) -> str | None:
    try:
        root = RAW_OUTPUT_DIR.resolve()
        target = Path(file_path).resolve()
        target.relative_to(root)
        return target.read_text(encoding="utf-8")
    except (OSError, ValueError):
        return None


def remove_raw_output_file(file_path: str | None) -> bool:
    """Remove one stored output only when it belongs to the application root."""
    if not file_path:
        return True
    try:
        root = RAW_OUTPUT_DIR.resolve()
        target = Path(file_path).resolve()
        target.relative_to(root)
        target.unlink(missing_ok=True)
    except (OSError, ValueError):
        return False
    return True


def has_task_raw_outputs(task_id: int) -> bool:
    root = RAW_OUTPUT_DIR.resolve()
    directory = root / f"task_{task_id}"
    try:
        directory.resolve().relative_to(root)
    except ValueError:
        return False
    try:
        return directory.is_dir() and any(entry.is_file() for entry in directory.iterdir())
    except OSError:
        return True


def discard_task_raw_outputs(task_id: int) -> int:
    """Remove the raw output files a deleted task left on disk.

    The database row is only half the record: the collected output is a file
    under raw_outputs/task_<id>. Dropping the task without this leaves orphans
    that nothing references and no retention pass will ever revisit, because
    the sweep walks inspection_results.
    """
    root = RAW_OUTPUT_DIR.resolve()
    directory = root / f"task_{task_id}"
    removed = 0
    try:
        # Guard against a task id that could escape the raw output root.
        directory.resolve().relative_to(root)
    except ValueError:
        return 0
    if not directory.is_dir():
        return 0
    for entry in directory.iterdir():
        if entry.is_file():
            try:
                entry.unlink()
                removed += 1
            except OSError:
                continue
    try:
        directory.rmdir()
    except OSError:
        # A file that could not be removed keeps the folder alive; the count
        # still reports what was cleared.
        pass
    return removed


def cleanup_orphaned_task_raw_outputs(session: Session) -> int:
    """Retry files left after a task row was deleted but Windows held a file open."""
    root = RAW_OUTPUT_DIR.resolve()
    if not root.is_dir():
        return 0
    active_task_ids = {task_id for (task_id,) in session.query(InspectionTask.id).all()}
    removed = 0
    for directory in root.glob("task_*"):
        if not directory.is_dir():
            continue
        try:
            task_id = int(directory.name.removeprefix("task_"))
            directory.resolve().relative_to(root)
        except (ValueError, OSError):
            continue
        if task_id in active_task_ids:
            continue
        removed += discard_task_raw_outputs(task_id)
    return removed


def cleanup_expired_raw_outputs(
    session: Session,
    *,
    retention_days: int,
    now,
) -> int:
    cutoff = now - timedelta(days=retention_days)
    results = session.query(InspectionResult).filter(
        InspectionResult.raw_output_ref.is_not(None),
        InspectionResult.created_at < cutoff,
    ).all()
    expired = 0
    root = RAW_OUTPUT_DIR.resolve()
    for result in results:
        try:
            target = Path(result.raw_output_ref).resolve()
            target.relative_to(root)
            target.unlink(missing_ok=True)
        except (OSError, ValueError):
            continue
        result.raw_output_ref = None
        result.raw_output_status = "expired"
        expired += 1
    if expired:
        session.add(ActivityLog(
            event="raw_output_retention_cleanup",
            message=f"Expired raw output files removed: {expired}",
        ))
    return expired
