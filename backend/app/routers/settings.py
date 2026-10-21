from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import DATA_DIR, SessionLocal, get_db
from app.models.device import ActivityLog
from app.schemas.settings import SettingsResponse, SettingsUpdate
from app.services.backup_paths import capture_legacy_backup_roots
from app.services.output_files import is_writable_directory
from app.services.raw_output_maintenance import run_raw_output_cleanup
from app.services.settings import get_settings


router = APIRouter()


@router.get("", response_model=SettingsResponse)
def read_settings(db: Session = Depends(get_db)):
    return get_settings(db)


@router.put("", response_model=SettingsResponse)
def update_settings(payload: SettingsUpdate, db: Session = Depends(get_db)):
    values = payload.model_dump(exclude_unset=True)
    for field, label in (("report_directory", "Report"), ("backup_directory", "Backup")):
        directory = values.get(field)
        if directory and not is_writable_directory(directory):
            raise HTTPException(status_code=422, detail=f"{label} directory is not writable")
    settings = get_settings(db)
    threshold_pairs = (
        ("cpu_warning_threshold", "cpu_critical_threshold", "CPU"),
        ("memory_warning_threshold", "memory_critical_threshold", "Memory"),
        ("storage_warning_threshold", "storage_critical_threshold", "Storage"),
    )
    for warning_field, critical_field, label in threshold_pairs:
        warning = values.get(warning_field, getattr(settings, warning_field))
        critical = values.get(critical_field, getattr(settings, critical_field))
        if critical < warning:
            raise HTTPException(status_code=422, detail=f"{label} critical threshold must not be lower than warning threshold")
    if "backup_directory" in values:
        old_root = Path(settings.backup_directory) if settings.backup_directory else DATA_DIR / "backups"
        capture_legacy_backup_roots(db, old_root)
    for field, value in values.items():
        setattr(settings, field, value)
    db.add(ActivityLog(event="settings_updated", message="Local inspection settings updated"))
    db.commit()
    db.refresh(settings)
    if "raw_output_retention_days" in values:
        run_raw_output_cleanup(SessionLocal, wait=True)
    return settings
