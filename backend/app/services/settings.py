from sqlalchemy.orm import Session

from app.models.device import AppSettings


TASK_EXECUTION_SETTING_FIELDS = (
    "max_concurrency",
    "snmp_timeout_seconds",
    "snmp_retries",
    "connection_timeout_seconds",
    "command_timeout_seconds",
    "network_retry_count",
    "cpu_warning_threshold",
    "cpu_critical_threshold",
    "memory_warning_threshold",
    "memory_critical_threshold",
    "storage_warning_threshold",
    "storage_critical_threshold",
)


def get_settings(db: Session) -> AppSettings:
    settings = db.get(AppSettings, 1)
    if settings is None:
        settings = AppSettings(id=1)
        db.add(settings)
        db.commit()
        db.refresh(settings)
    return settings


def snapshot_task_settings(settings: AppSettings) -> dict:
    return {field: getattr(settings, field) for field in TASK_EXECUTION_SETTING_FIELDS}


def restore_task_settings(snapshot: dict | None, fallback: AppSettings) -> AppSettings:
    if not snapshot:
        return fallback
    return AppSettings(**{
        field: snapshot.get(field, getattr(fallback, field))
        for field in TASK_EXECUTION_SETTING_FIELDS
    })
