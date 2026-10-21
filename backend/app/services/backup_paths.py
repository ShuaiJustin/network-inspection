from pathlib import Path

from sqlalchemy.orm import Session

from app.models.device import BackupRecord


def resolve_backup_path(file_path: str, root_dir: Path) -> Path | None:
    try:
        root = root_dir.resolve()
        candidate = Path(file_path).resolve()
        candidate.relative_to(root)
    except (OSError, ValueError):
        return None
    return candidate


def resolve_backup_file(record: BackupRecord, fallback_root: Path) -> Path | None:
    if not record.file_path:
        return None
    root = Path(record.backup_root) if record.backup_root else fallback_root
    if record.backup_root and not root.is_absolute():
        return None
    return resolve_backup_path(record.file_path, root)


def capture_legacy_backup_roots(session: Session, root_dir: Path) -> None:
    try:
        root = root_dir.resolve()
    except (OSError, ValueError):
        return
    records = session.query(BackupRecord).filter(
        BackupRecord.backup_root.is_(None),
        BackupRecord.file_path.is_not(None),
        BackupRecord.status == "success",
    ).yield_per(500)
    for record in records:
        path = resolve_backup_path(record.file_path, root)
        if path is not None:
            record.backup_root = str(root)
            record.file_path = str(path)
