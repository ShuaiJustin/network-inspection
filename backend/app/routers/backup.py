import difflib
import errno
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import DATA_DIR, get_db
from app.models.device import ActivityLog, BackupRecord, Device
from app.services.backup_paths import resolve_backup_file
from app.services.collectors import backup_configuration
from app.services.configuration_validation import validate_configuration
from app.services.device_operations import device_operation_lock
from app.services.maintenance import backup_operation
from app.services.output_files import ensure_available_space, reserved_unique_file_path
from app.services.security import redact_configuration_text, redact_runtime_text
from app.services.settings import get_settings
from app.services.time import utc_for_api


router = APIRouter()
BACKUP_DIR = DATA_DIR / "backups"
BACKUP_DIR.mkdir(exist_ok=True)


def _backup_root(settings) -> Path:
    return Path(settings.backup_directory) if settings.backup_directory else BACKUP_DIR


def prune_expired_backups(
    db: Session,
    device_id: int,
    keep_count: int,
    root_dir: Path | None = None,
) -> None:
    root_dir = root_dir or BACKUP_DIR
    expired = (
        db.query(BackupRecord)
        .filter(BackupRecord.device_id == device_id, BackupRecord.status == "success")
        .order_by(BackupRecord.created_at.desc(), BackupRecord.id.desc())
        .offset(keep_count)
        .all()
    )
    for record in expired:
        if record.file_path:
            path = resolve_backup_file(record, root_dir)
            if path:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    continue
        db.delete(record)


def _expired_backup_records(
    db: Session,
    device_id: int,
    keep_count: int,
) -> list[BackupRecord]:
    return (
        db.query(BackupRecord)
        .filter(BackupRecord.device_id == device_id, BackupRecord.status == "success")
        .order_by(BackupRecord.created_at.desc(), BackupRecord.id.desc())
        .offset(keep_count)
        .all()
    )


def _delete_backup_file(record: BackupRecord, root_dir: Path) -> bool:
    if not record.file_path:
        return True
    path = resolve_backup_file(record, root_dir)
    if not path:
        return False
    try:
        path.unlink(missing_ok=True)
    except OSError:
        return False
    return True


def _finalize_expired_backups(
    db: Session,
    records: list[BackupRecord],
    root_dir: Path,
) -> None:
    """Delete old files only after the new DB state is durable.

    If a file cannot be removed, its record remains a successful record and is
    considered again by the next backup. This preserves both the history row
    and the file when Windows has the file open or the directory is offline.
    """
    deleted = False
    for record in records:
        if _delete_backup_file(record, root_dir):
            db.delete(record)
            deleted = True
    if not deleted:
        return
    try:
        db.commit()
    except Exception:
        # A missing file is harmless on the next retry; keeping the row here is
        # preferable to losing the history during a transient DB failure.
        db.rollback()


def _safe_filename_part(value: str) -> str:
    return re.sub(r'[\\/:*?"<>|]+', "_", value).strip(" ._") or "device"


def _open_directory(directory: Path) -> None:
    if os.name != "nt":
        raise OSError("Opening backup folders is only available on Windows")
    os.startfile(str(directory))


def _remove_ignored_dynamic_lines(lines: list[str], patterns: list[str]) -> list[str]:
    expressions = []
    for pattern in patterns:
        try:
            expressions.append(re.compile(pattern))
        except re.error:
            continue
    return [line for line in lines if not any(expression.search(line) for expression in expressions)]


def _validate_backup_output(
    output: str,
    vendor: str | None = None,
    platform_id: str | None = None,
) -> str | None:
    return validate_configuration(output, vendor, platform_id)


@router.get("")
def list_backups(db: Session = Depends(get_db)):
    return [
        {"id": record.id, "device_id": record.device_id, "device_ip": record.device_ip, "file_path": record.file_path, "status": record.status, "message": record.message, "created_at": utc_for_api(record.created_at)}
        for record in db.query(BackupRecord).order_by(BackupRecord.id.desc()).all()
    ]


@router.post("")
async def create_backups(
    device_ids: Annotated[list[int], Body(min_length=1, max_length=500)],
    db: Session = Depends(get_db),
):
    async with backup_operation():
        return await _create_backups(device_ids, db)


@router.delete("/{backup_id}")
def delete_backup(backup_id: int, db: Session = Depends(get_db)):
    record = db.get(BackupRecord, backup_id)
    if not record:
        raise HTTPException(status_code=404, detail="Backup record not found")

    if record.file_path:
        path = resolve_backup_file(record, _backup_root(get_settings(db)))
        if path is None:
            raise HTTPException(
                status_code=409,
                detail="Backup file is outside the configured backup root",
            )
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            raise HTTPException(status_code=500, detail="Could not delete backup file") from exc

    device_ip = record.device_ip
    db.delete(record)
    db.add(ActivityLog(
        event="backup_deleted",
        device_ip=device_ip,
        message="Configuration backup record deleted",
    ))
    db.commit()
    return {"deleted": 1}


async def _create_backups(device_ids: list[int], db: Session):
    devices = db.query(Device).filter(Device.id.in_(device_ids)).all()
    if len(devices) != len(set(device_ids)):
        raise HTTPException(status_code=404, detail="One or more devices were not found")
    device_snapshots = [
        {
            "id": device.id,
            "ip": device.ip,
            "name": device.name,
            "vendor": device.vendor,
        }
        for device in devices
    ]
    settings = get_settings(db)
    root_dir = _backup_root(settings).resolve()
    root_dir.mkdir(parents=True, exist_ok=True)
    connection_timeout = settings.connection_timeout_seconds
    command_timeout = settings.command_timeout_seconds
    retention_count = settings.backup_retention_count
    records = []
    expired_record_ids: set[int] = set()
    for snapshot in device_snapshots:
        pending_file: Path | None = None
        try:
            device_id = snapshot["id"]
            async with device_operation_lock(device_id):
                device = (
                    db.query(Device)
                    .populate_existing()
                    .filter(Device.id == device_id)
                    .first()
                )
                if not device:
                    record = BackupRecord(
                        device_id=None,
                        device_ip=snapshot["ip"],
                        status="failed",
                        message="Device not found",
                    )
                    db.add(record)
                    db.commit()
                    records.append(record)
                    continue
                result = await backup_configuration(
                    device,
                    connection_timeout,
                    command_timeout,
                )
                record = BackupRecord(
                    device_id=device.id,
                    device_ip=device.ip,
                    status="failed",
                    message=redact_runtime_text(result.message) if result.message else None,
                )
                validation_error = (
                    _validate_backup_output(result.raw_output, device.vendor, getattr(device, "platform_id", None))
                    if result.ok else None
                )
                if validation_error:
                    record.message = validation_error
                elif result.ok:
                    device_dir = root_dir / device.vendor / device.ip
                    device_dir.mkdir(parents=True, exist_ok=True)
                    filename = f"{_safe_filename_part(device.name or device.ip)}_{datetime.now():%Y%m%d_%H%M%S}.txt"
                    output = redact_configuration_text(result.raw_output)
                    try:
                        with reserved_unique_file_path(device_dir, filename) as file_path:
                            temporary_path = device_dir / f".{uuid4().hex}.tmp"
                            try:
                                ensure_available_space(device_dir, len(output.encode("utf-8")))
                                temporary_path.write_text(output, encoding="utf-8")
                                temporary_path.replace(file_path)
                            except BaseException:
                                try:
                                    temporary_path.unlink(missing_ok=True)
                                except OSError:
                                    pass
                                raise
                    except OSError as exc:
                        record.message = "磁盘可用空间不足，无法保存备份" if exc.errno == errno.ENOSPC else f"Could not save backup: {exc}"
                        db.add(ActivityLog(
                            level="ERROR",
                            event="backup_save_failed",
                            device_ip=device.ip,
                            message=record.message,
                        ))
                    else:
                        record.status = "success"
                        record.file_path = str(file_path)
                        record.backup_root = str(root_dir)
                        pending_file = file_path
                if not result.ok or validation_error:
                    db.add(ActivityLog(
                        level="ERROR",
                        event="backup_device_failed",
                        device_ip=device.ip,
                        message=record.message,
                    ))
                db.add(record)
                db.commit()
                pending_file = None
                records.append(record)
                if record.status == "success":
                    expired_record_ids.update(
                        expired.id
                        for expired in _expired_backup_records(
                            db, device.id, retention_count
                        )
                    )
        except Exception as exc:
            db.rollback()
            if pending_file:
                try:
                    pending_file.unlink(missing_ok=True)
                except OSError:
                    pass
            message = redact_runtime_text(
                f"Configuration backup failed unexpectedly: {type(exc).__name__}: {exc}"
            )
            async with device_operation_lock(snapshot["id"]):
                current = db.query(Device).filter(Device.id == snapshot["id"]).first()
                record = BackupRecord(
                    device_id=current.id if current else None,
                    device_ip=snapshot["ip"],
                    status="failed",
                    message=message,
                )
                db.add(record)
                db.add(ActivityLog(
                    level="ERROR",
                    event="backup_device_error",
                    device_ip=snapshot["ip"],
                    message=message,
                ))
                db.commit()
                records.append(record)
    db.add(ActivityLog(event="backup_finished", message=f"Backup completed for {len(devices)} devices"))
    db.commit()
    expired_records = (
        db.query(BackupRecord).filter(BackupRecord.id.in_(expired_record_ids)).all()
        if expired_record_ids
        else []
    )
    _finalize_expired_backups(db, expired_records, root_dir)
    return {"records": [{"device_ip": record.device_ip, "status": record.status, "message": record.message} for record in records]}


@router.get("/{backup_id}/diff")
def compare_backup(backup_id: int, db: Session = Depends(get_db)):
    current = db.get(BackupRecord, backup_id)
    if not current or current.status != "success" or not current.file_path:
        raise HTTPException(status_code=404, detail="Valid backup not found")
    previous = (
        db.query(BackupRecord)
        .filter(
            BackupRecord.device_id == current.device_id,
            BackupRecord.device_ip == current.device_ip if current.device_id is None else True,
            BackupRecord.status == "success",
            BackupRecord.id < current.id,
        )
        .order_by(BackupRecord.id.desc())
        .first()
    )
    if not previous or not previous.file_path:
        return {"diff": [], "message": "No previous valid backup"}
    settings = get_settings(db)
    root_dir = _backup_root(settings)
    previous_path = resolve_backup_file(previous, root_dir)
    current_path = resolve_backup_file(current, root_dir)
    if not previous_path or not current_path or not previous_path.is_file() or not current_path.is_file():
        raise HTTPException(status_code=404, detail="Backup file is no longer available")
    try:
        before = _remove_ignored_dynamic_lines(
            previous_path.read_text(encoding="utf-8").splitlines(),
            settings.backup_ignore_patterns or [],
        )
        after = _remove_ignored_dynamic_lines(
            current_path.read_text(encoding="utf-8").splitlines(),
            settings.backup_ignore_patterns or [],
        )
    except (OSError, UnicodeError) as exc:
        raise HTTPException(status_code=404, detail="Backup file is no longer available") from exc
    return {
        "diff": [
            redact_configuration_text(line)
            for line in difflib.unified_diff(before, after, fromfile="previous", tofile="current", lineterm="")
        ]
    }


@router.post("/{backup_id}/open-directory")
def open_backup_directory(backup_id: int, db: Session = Depends(get_db)):
    record = db.get(BackupRecord, backup_id)
    if not record or record.status != "success" or not record.file_path:
        raise HTTPException(status_code=404, detail="Valid backup not found")
    settings = get_settings(db)
    path = resolve_backup_file(record, _backup_root(settings))
    if not path:
        raise HTTPException(status_code=404, detail="Backup file is no longer available")
    directory = path.parent
    if not path.is_file() or not directory.is_dir():
        raise HTTPException(status_code=404, detail="Backup file is no longer available")
    try:
        _open_directory(directory)
    except OSError as exc:
        db.add(ActivityLog(
            level="ERROR",
            event="backup_directory_open_failed",
            device_ip=record.device_ip,
            message=f"Could not open backup directory: {exc}",
        ))
        db.commit()
        raise HTTPException(status_code=500, detail="Could not open backup directory") from exc
    db.add(ActivityLog(
        event="backup_directory_opened",
        device_ip=record.device_ip,
        message=f"Backup directory opened: {directory}",
    ))
    db.commit()
    return {"directory": str(directory)}
