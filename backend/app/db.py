from datetime import datetime
import os
from pathlib import Path
import shutil
import sqlite3
import sys

from sqlalchemy import MetaData, create_engine, event, inspect, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool
from sqlalchemy.schema import CreateIndex, CreateTable

from app.models.device import ActivityLog, AppSettings, BackupRecord, Base, Device, InspectionResult, InspectionTask
from app.services.activity_logs import persist_activity_logs
from app.services.vendor_registry import VENDORS


if configured_data_dir := os.environ.get("NETWORK_INSPECTION_DATA_DIR"):
    DATA_DIR = Path(configured_data_dir)
elif getattr(sys, "frozen", False):
    DATA_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "NetworkInspection"
else:
    DATA_DIR = Path(__file__).resolve().parents[1] / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
DATABASE_PATH = DATA_DIR / "network_inspection.db"
DATABASE_URL = f"sqlite:///{DATABASE_PATH.as_posix()}"
ACTIVITY_LOG_DIR = DATA_DIR / "logs"
MIGRATION_BACKUP_DIR = DATA_DIR / "database_backups"

engine_options = {"connect_args": {"check_same_thread": False}}
if os.environ.get("NETWORK_INSPECTION_DISABLE_DB_POOL") == "1":
    engine_options["poolclass"] = NullPool
engine = create_engine(DATABASE_URL, **engine_options)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@event.listens_for(engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
    finally:
        cursor.close()


def _capture_pending_activity_logs(session: Session, _flush_context) -> None:
    captured = session.info.setdefault("activity_file_log_ids", set())
    pending = session.info.setdefault("activity_file_logs", [])
    for record in session.new:
        if isinstance(record, ActivityLog) and record.id not in captured:
            pending.append({
                "id": record.id,
                "level": record.level,
                "event": record.event,
                "device_ip": record.device_ip,
                "task_id": record.task_id,
                "message": record.message,
                "created_at": record.created_at,
            })
            captured.add(record.id)


def _configured_log_retention_days() -> int:
    try:
        with sqlite3.connect(DATABASE_PATH) as connection:
            row = connection.execute("SELECT log_retention_days FROM app_settings WHERE id = 1").fetchone()
        return max(1, int(row[0])) if row else 30
    except (OSError, sqlite3.Error, TypeError, ValueError):
        return 30


def _write_committed_activity_logs(session: Session) -> None:
    records = session.info.pop("activity_file_logs", [])
    session.info.pop("activity_file_log_ids", None)
    persist_activity_logs(records, ACTIVITY_LOG_DIR, _configured_log_retention_days())


def _discard_pending_activity_logs(session: Session) -> None:
    session.info.pop("activity_file_logs", None)
    session.info.pop("activity_file_log_ids", None)


event.listen(SessionLocal, "after_flush", _capture_pending_activity_logs)
event.listen(SessionLocal, "after_commit", _write_committed_activity_logs)
event.listen(SessionLocal, "after_rollback", _discard_pending_activity_logs)


def create_migration_backup(database_path: Path, backup_directory: Path) -> Path:
    backup_directory.mkdir(parents=True, exist_ok=True)
    backup_path = backup_directory / f"migration_{datetime.now():%Y%m%d_%H%M%S_%f}.db"
    temporary_path = backup_path.with_suffix(".tmp")
    source = None
    target = None
    try:
        source = sqlite3.connect(database_path)
        target = sqlite3.connect(temporary_path)
        source.backup(target)
        target.close()
        target = None
        source.close()
        source = None
        os.replace(temporary_path, backup_path)
    except Exception:
        if target is not None:
            target.close()
        if source is not None:
            source.close()
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return backup_path


def _schema_migration_needed(database_engine) -> bool:
    inspector = inspect(database_engine)
    required_columns = {
        "devices": {"ssh_host_fingerprint", "platform_id"},
        "app_settings": {
            "backup_ignore_patterns", "storage_warning_threshold", "raw_output_retention_days",
            "cpu_critical_threshold", "memory_critical_threshold", "storage_critical_threshold",
        },
        "inspection_tasks": {"selected_device_snapshot", "settings_snapshot"},
        "inspection_results": {"raw_output_ref", "raw_output_status", "raw_output_bytes"},
        "backup_records": {"backup_root"},
    }
    for table_name, columns in required_columns.items():
        if table_name not in inspector.get_table_names():
            continue
        existing_columns = {column["name"] for column in inspector.get_columns(table_name)}
        if not columns.issubset(existing_columns):
            return True
    for table_name in ("inspection_results", "backup_records"):
        if table_name not in inspector.get_table_names():
            continue
        device_foreign_key = next(
            (
                foreign_key
                for foreign_key in inspector.get_foreign_keys(table_name)
                if foreign_key.get("referred_table") == "devices"
            ),
            None,
        )
        if not device_foreign_key or (
            device_foreign_key.get("options", {}).get("ondelete", "").upper() != "SET NULL"
        ):
            return True
    return False


def _rebuild_device_history_table(database_engine, model_table) -> None:
    table_name = model_table.name
    temporary_name = f"{table_name}__device_fk_migration"
    migration_metadata = MetaData()
    for table in Base.metadata.sorted_tables:
        table.to_metadata(migration_metadata)
    temporary_table = model_table.to_metadata(migration_metadata, name=temporary_name)
    column_names = [column.name for column in model_table.columns]
    quoted_columns = ", ".join(f'"{column_name}"' for column_name in column_names)

    with database_engine.begin() as connection:
        connection.execute(text(
            f'UPDATE "{table_name}" SET device_id = NULL '
            'WHERE device_id IS NOT NULL '
            'AND NOT EXISTS (SELECT 1 FROM devices WHERE devices.id = device_id)'
        ))
        connection.execute(CreateTable(temporary_table))
        connection.execute(text(
            f'INSERT INTO "{temporary_name}" ({quoted_columns}) '
            f'SELECT {quoted_columns} FROM "{table_name}"'
        ))
        connection.execute(text(f'DROP TABLE "{table_name}"'))
        connection.execute(text(
            f'ALTER TABLE "{temporary_name}" RENAME TO "{table_name}"'
        ))
        for index in model_table.indexes:
            connection.execute(CreateIndex(index))


def _apply_schema_migration_steps(database_engine) -> None:
    device_columns = {column["name"] for column in inspect(database_engine).get_columns("devices")}
    if "ssh_host_fingerprint" not in device_columns:
        with database_engine.begin() as connection:
            connection.execute(text("ALTER TABLE devices ADD COLUMN ssh_host_fingerprint VARCHAR(128)"))
    if "platform_id" not in device_columns:
        with database_engine.begin() as connection:
            connection.execute(text("ALTER TABLE devices ADD COLUMN platform_id VARCHAR(80)"))
            for vendor in VENDORS.values():
                connection.execute(
                    text("UPDATE devices SET platform_id = :platform_id WHERE vendor = :vendor AND platform_id IS NULL"),
                    {"platform_id": vendor.default_platform_id, "vendor": vendor.id},
                )
    settings_columns = {column["name"] for column in inspect(database_engine).get_columns("app_settings")}
    if "backup_ignore_patterns" not in settings_columns:
        with database_engine.begin() as connection:
            connection.execute(text("ALTER TABLE app_settings ADD COLUMN backup_ignore_patterns JSON DEFAULT '[]'"))
    if "storage_warning_threshold" not in settings_columns:
        with database_engine.begin() as connection:
            connection.execute(text("ALTER TABLE app_settings ADD COLUMN storage_warning_threshold INTEGER DEFAULT 80"))
    for column_name, default in (
        ("cpu_critical_threshold", 95),
        ("memory_critical_threshold", 95),
        ("storage_critical_threshold", 95),
    ):
        if column_name not in settings_columns:
            with database_engine.begin() as connection:
                connection.execute(text(f"ALTER TABLE app_settings ADD COLUMN {column_name} INTEGER DEFAULT {default}"))
    if "raw_output_retention_days" not in settings_columns:
        with database_engine.begin() as connection:
            connection.execute(text("ALTER TABLE app_settings ADD COLUMN raw_output_retention_days INTEGER DEFAULT 30"))
    task_columns = {column["name"] for column in inspect(database_engine).get_columns("inspection_tasks")}
    if "selected_device_snapshot" not in task_columns:
        with database_engine.begin() as connection:
            connection.execute(text("ALTER TABLE inspection_tasks ADD COLUMN selected_device_snapshot JSON"))
    if "settings_snapshot" not in task_columns:
        with database_engine.begin() as connection:
            connection.execute(text("ALTER TABLE inspection_tasks ADD COLUMN settings_snapshot JSON"))
    result_columns = {column["name"] for column in inspect(database_engine).get_columns("inspection_results")}
    for column_name, definition in (
        ("raw_output_ref", "TEXT"),
        ("raw_output_status", "VARCHAR(30) DEFAULT 'not_available'"),
        ("raw_output_bytes", "INTEGER DEFAULT 0"),
    ):
        if column_name not in result_columns:
            with database_engine.begin() as connection:
                connection.execute(text(f"ALTER TABLE inspection_results ADD COLUMN {column_name} {definition}"))
    backup_columns = {column["name"] for column in inspect(database_engine).get_columns("backup_records")}
    if "backup_root" not in backup_columns:
        with database_engine.begin() as connection:
            connection.execute(text("ALTER TABLE backup_records ADD COLUMN backup_root TEXT"))
    inspector = inspect(database_engine)
    for model_table in (InspectionResult.__table__, BackupRecord.__table__):
        device_foreign_key = next(
            (
                foreign_key
                for foreign_key in inspector.get_foreign_keys(model_table.name)
                if foreign_key.get("referred_table") == "devices"
            ),
            None,
        )
        if not device_foreign_key or (
            device_foreign_key.get("options", {}).get("ondelete", "").upper() != "SET NULL"
        ):
            _rebuild_device_history_table(database_engine, model_table)
    if "backup_root" not in backup_columns:
        from app.services.backup_paths import capture_legacy_backup_roots

        with Session(database_engine) as session:
            configured_root = session.query(AppSettings.backup_directory).filter(AppSettings.id == 1).scalar()
            root = Path(configured_root) if configured_root else Path(database_engine.url.database).resolve().parent / "backups"
            capture_legacy_backup_roots(session, root)
            session.commit()


def apply_schema_migrations(database_engine, database_path: Path, backup_directory: Path) -> Path | None:
    if not database_path.exists() or not _schema_migration_needed(database_engine):
        return None
    backup_path = create_migration_backup(database_path, backup_directory)
    try:
        _apply_schema_migration_steps(database_engine)
    except Exception as exc:
        restore_path = database_path.with_suffix(".restore.tmp")
        database_engine.dispose()
        try:
            shutil.copy2(backup_path, restore_path)
            os.replace(restore_path, database_path)
        finally:
            restore_path.unlink(missing_ok=True)
        raise RuntimeError(f"Database migration failed and was restored from {backup_path}") from exc
    return backup_path


def init_db() -> None:
    inspector = inspect(engine)
    if "devices" in inspector.get_table_names():
        current_columns = {column["name"] for column in inspector.get_columns("devices")}
        required_columns = {"vendor", "device_type", "ssh_password", "snmp_community"}
        if not required_columns.issubset(current_columns):
            create_migration_backup(DATABASE_PATH, MIGRATION_BACKUP_DIR)
            engine.dispose()
            legacy_path = DATA_DIR / f"network_inspection_legacy_{datetime.now():%Y%m%d_%H%M%S}.db"
            (DATA_DIR / "network_inspection.db").replace(legacy_path)
    Base.metadata.create_all(bind=engine)
    apply_schema_migrations(engine, DATABASE_PATH, MIGRATION_BACKUP_DIR)
    _cleanup_expired_raw_outputs()
    _cleanup_expired_activity_logs()
    recover_interrupted_tasks()


def _cleanup_expired_raw_outputs() -> None:
    from app.services.raw_output_maintenance import run_raw_output_cleanup

    run_raw_output_cleanup(SessionLocal)


def _cleanup_expired_activity_logs() -> None:
    from app.services.log_maintenance import run_activity_log_cleanup

    run_activity_log_cleanup(SessionLocal)


def recover_interrupted_tasks() -> int:
    session = SessionLocal()
    try:
        tasks = session.query(InspectionTask).filter(
            (InspectionTask.status.in_(("queued", "running")))
            | ((InspectionTask.status == "cancelled") & InspectionTask.finished_at.is_(None))
        ).all()
        if not tasks:
            return 0
        now = datetime.utcnow()
        for task in tasks:
            if task.status == "cancelled":
                from app.services.inspection import (
                    FAILED_CONCLUSIONS,
                    SUCCESS_CONCLUSIONS,
                    _cancelled_device_result,
                )

                existing_results = list(task.results)
                completed_ids = {result.device_id for result in existing_results if result.device_id is not None}
                completed_ips = {result.device_ip for result in existing_results}
                added_results = []
                selected_ids = [int(value) for value in task.selected_device_ids.split(",") if value]
                current_devices = session.query(Device).filter(Device.id.in_(selected_ids)).all()
                current_by_id = {device.id: device for device in current_devices}
                snapshots = task.selected_device_snapshot or [
                    {
                        "id": device_id,
                        "name": current_by_id[device_id].name if device_id in current_by_id else None,
                        "ip": current_by_id[device_id].ip if device_id in current_by_id else "-",
                        "vendor": current_by_id[device_id].vendor if device_id in current_by_id else None,
                    }
                    for device_id in selected_ids
                ]
                for snapshot in snapshots:
                    if snapshot.get("id") in completed_ids or snapshot.get("ip") in completed_ips:
                        continue
                    result = _cancelled_device_result(
                        task,
                        snapshot,
                        snapshot.get("id") in current_by_id,
                    )
                    session.add(result)
                    added_results.append(result)
                conclusions = [result.conclusion for result in (*existing_results, *added_results)]
                task.completed_devices = len(conclusions)
                task.success_devices = sum(
                    conclusion in SUCCESS_CONCLUSIONS
                    for conclusion in conclusions
                )
                task.failed_devices = sum(
                    conclusion in FAILED_CONCLUSIONS
                    for conclusion in conclusions
                )
                task.finished_at = now
                session.add(ActivityLog(
                    level="WARNING",
                    event="task_cancelled_during_shutdown",
                    task_id=task.id,
                    message="Cancelled task finalized after the desktop application restarted",
                ))
                continue
            from app.services.inspection import (
                FAILED_CONCLUSIONS,
                SUCCESS_CONCLUSIONS,
                _task_failed_device_result,
            )

            existing_results = list(task.results)
            completed_ids = {result.device_id for result in existing_results if result.device_id is not None}
            completed_ips = {result.device_ip for result in existing_results}
            selected_ids = [
                int(value) for value in task.selected_device_ids.split(",") if value
            ]
            current_devices = session.query(Device).filter(Device.id.in_(selected_ids)).all()
            current_by_id = {device.id: device for device in current_devices}
            snapshots = task.selected_device_snapshot or [
                {
                    "id": device_id,
                    "name": current_by_id[device_id].name if device_id in current_by_id else None,
                    "ip": current_by_id[device_id].ip if device_id in current_by_id else "-",
                    "vendor": current_by_id[device_id].vendor if device_id in current_by_id else None,
                }
                for device_id in selected_ids
            ]
            added_results: list[InspectionResult] = []
            for snapshot in snapshots:
                if snapshot.get("id") in completed_ids or snapshot.get("ip") in completed_ips:
                    continue
                result = _task_failed_device_result(
                    snapshot,
                    "巡检过程中应用退出，设备未完成采集",
                    snapshot.get("id") in current_by_id,
                )
                result.task_id = task.id
                session.add(result)
                added_results.append(result)
            conclusions = [result.conclusion for result in (*existing_results, *added_results)]
            task.status = "failed"
            task.completed_devices = len(conclusions)
            task.success_devices = sum(
                conclusion in SUCCESS_CONCLUSIONS for conclusion in conclusions
            )
            task.failed_devices = sum(
                conclusion in FAILED_CONCLUSIONS for conclusion in conclusions
            )
            task.finished_at = now
            session.add(ActivityLog(
                level="ERROR",
                event="task_interrupted",
                task_id=task.id,
                message="Task marked failed after the desktop application exited before completion",
            ))
        session.commit()
        return len(tasks)
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
