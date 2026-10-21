import logging
import re
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from app.db import DATA_DIR, DATABASE_PATH, SessionLocal, engine, get_db, init_db
from app.models.device import ActivityLog, Base, InspectionTask
from app.routers.auth import clear_sessions
from app.services.maintenance import (
    ActiveConfigurationBackupError,
    database_access_operation,
    database_restore_operation,
)


router = APIRouter()
DATABASE_BACKUP_DIR = DATA_DIR / "database_backups"
DATABASE_BACKUP_DIR.mkdir(exist_ok=True)
REQUIRED_TABLES = {table.name for table in Base.metadata.sorted_tables}
# The preceding release has all runtime columns except this new nullable field.
RESTORE_OPTIONAL_COLUMNS = {"backup_records": {"backup_root"}}
REQUIRED_COLUMNS = {
    table.name: {column.name for column in table.columns} - RESTORE_OPTIONAL_COLUMNS.get(table.name, set())
    for table in Base.metadata.sorted_tables
}
ALLOWED_INDEX_DEFINITIONS = {
    index.name: (
        table.name,
        tuple(column.name for column in index.columns),
        bool(index.unique),
    )
    for table in Base.metadata.sorted_tables
    for index in table.indexes
    if index.name
}
MAX_DATABASE_RESTORE_SIZE = 512 * 1024 * 1024
PRE_RESTORE_BACKUP_KEEP_COUNT = 10
logger = logging.getLogger(__name__)


def _prune_pre_restore_backups(current_snapshot: Path) -> None:
    try:
        root = DATABASE_BACKUP_DIR.resolve()
        current = current_snapshot.resolve()
        if current.parent != root or not current.is_file():
            return
        snapshots = sorted(
            (
                path for path in root.iterdir()
                if re.fullmatch(r"pre_restore_\d{8}_\d{6}_\d{6}\.db", path.name)
                and not path.is_symlink()
                and path.is_file()
                and path != current
            ),
            key=lambda path: path.name,
            reverse=True,
        )
    except OSError:
        logger.warning("Could not list pre-restore snapshots", exc_info=True)
        return
    # Keep the just-created copy even if the system clock moved backwards.
    for path in snapshots[PRE_RESTORE_BACKUP_KEEP_COUNT - 1:]:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.warning("Could not remove old pre-restore snapshot: %s", path, exc_info=True)


def create_database_snapshot(destination: Path) -> None:
    source = sqlite3.connect(str(DATABASE_PATH))
    target = sqlite3.connect(str(destination))
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()


def replace_database_file(source: Path, destination: Path) -> None:
    source_connection = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)
    destination_connection = sqlite3.connect(str(destination), timeout=30)
    try:
        source_connection.backup(destination_connection)
    finally:
        destination_connection.close()
        source_connection.close()


def validate_database_backup(path: Path) -> None:
    try:
        connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        try:
            try:
                integrity = connection.execute("PRAGMA integrity_check").fetchall()
            except sqlite3.Error as exc:
                raise HTTPException(
                    status_code=400,
                    detail="SQLite backup failed integrity check",
                ) from exc
            if integrity != [("ok",)]:
                raise HTTPException(status_code=400, detail="SQLite backup failed integrity check")
            schema_objects = [
                row
                for row in connection.execute(
                    "SELECT type, name, tbl_name, sql FROM sqlite_master"
                )
                if not row[1].startswith("sqlite_")
            ]
            tables = {row[1] for row in schema_objects if row[0] == "table"}
            indexes = {row[1] for row in schema_objects if row[0] == "index"}
            invalid_object = any(
                object_type not in {"table", "index"}
                or (object_type == "table" and name not in REQUIRED_TABLES)
                or (object_type == "index" and name not in ALLOWED_INDEX_DEFINITIONS)
                or (
                    object_type == "table"
                    and sql
                    and re.match(r"\s*CREATE\s+VIRTUAL\s+TABLE\b", sql, re.IGNORECASE)
                )
                for object_type, name, _table_name, sql in schema_objects
            )
            if invalid_object:
                raise HTTPException(
                    status_code=400,
                    detail="Backup file contains unsupported SQLite schema objects",
                )
            if tables != REQUIRED_TABLES:
                raise HTTPException(
                    status_code=400,
                    detail="Backup file does not match the inspection database schema",
                )
            if not set(ALLOWED_INDEX_DEFINITIONS).issubset(indexes):
                raise HTTPException(
                    status_code=400,
                    detail="Backup file does not match the inspection database schema",
                )
            for object_type, name, table_name, _sql in schema_objects:
                if object_type != "index":
                    continue
                expected_table, expected_columns, expected_unique = ALLOWED_INDEX_DEFINITIONS[name]
                index_info = list(connection.execute(f'PRAGMA index_info("{name}")'))
                actual_columns = tuple(row[2] for row in index_info)
                index_rows = {
                    row[1]: (bool(row[2]), row[3], bool(row[4]))
                    for row in connection.execute(f'PRAGMA index_list("{table_name}")')
                }
                actual_unique, origin, partial = index_rows.get(name, (None, None, None))
                if (
                    table_name != expected_table
                    or actual_columns != expected_columns
                    or any(row[1] < 0 or row[2] is None for row in index_info)
                    or actual_unique != expected_unique
                    or origin != "c"
                    or partial is not False
                ):
                    raise HTTPException(
                        status_code=400,
                        detail="Backup file contains unsupported SQLite schema objects",
                    )
            columns = {
                table: {row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')}
                for table in REQUIRED_COLUMNS
            }
        finally:
            connection.close()
    except HTTPException:
        raise
    except sqlite3.Error as exc:
        raise HTTPException(status_code=400, detail="Invalid SQLite backup file") from exc
    if tables != REQUIRED_TABLES:
        raise HTTPException(status_code=400, detail="Backup file does not match the inspection database schema")
    if any(
        table in tables and not required.issubset(columns.get(table, set()))
        for table, required in REQUIRED_COLUMNS.items()
    ):
        raise HTTPException(status_code=400, detail="Backup file does not match the inspection database schema")


@router.get("/database-backup")
def download_database_backup(db: Session = Depends(get_db)):
    DATABASE_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    download_name = f"network_inspection_{datetime.now():%Y%m%d_%H%M%S}.db"
    with tempfile.NamedTemporaryFile(
        prefix="database_download_",
        suffix=".db",
        dir=DATABASE_BACKUP_DIR,
        delete=False,
    ) as temporary:
        output = Path(temporary.name)
    try:
        create_database_snapshot(output)
        db.add(ActivityLog(event="database_backup", message=f"Database backup created: {download_name}"))
        db.commit()
    except Exception:
        output.unlink(missing_ok=True)
        raise
    return FileResponse(
        output,
        media_type="application/vnd.sqlite3",
        filename=download_name,
        background=BackgroundTask(output.unlink, missing_ok=True),
    )


@router.post("/database-restore")
async def restore_database_backup(
    file: UploadFile = File(...),
    confirmed: bool = Form(default=False),
    db: Session = Depends(get_db),
):
    if not confirmed:
        async with database_access_operation():
            db.add(ActivityLog(
                level="WARNING",
                event="database_restore_cancelled",
                message="Database restore request was not confirmed",
            ))
            db.commit()
        await file.close()
        raise HTTPException(status_code=400, detail="Database restore requires confirmation")
    if not file.filename or not file.filename.lower().endswith((".db", ".sqlite", ".sqlite3")):
        await file.close()
        raise HTTPException(status_code=400, detail="Please upload an SQLite backup file")
    temporary_path: Path | None = None
    safety_copy: Path | None = None
    try:
        async with database_restore_operation():
            if db.query(InspectionTask).filter(
                (InspectionTask.status.in_(("queued", "running")))
                | ((InspectionTask.status == "cancelled") & InspectionTask.finished_at.is_(None))
            ).first():
                raise HTTPException(status_code=409, detail="Cannot restore while an inspection task is running")
            with tempfile.NamedTemporaryFile(suffix=".db", dir=DATABASE_BACKUP_DIR, delete=False) as temporary:
                temporary_path = Path(temporary.name)
                copied = 0
                while chunk := await file.read(min(1024 * 1024, MAX_DATABASE_RESTORE_SIZE + 1 - copied)):
                    copied += len(chunk)
                    if copied > MAX_DATABASE_RESTORE_SIZE:
                        raise HTTPException(status_code=413, detail="Database backup file is too large")
                    temporary.write(chunk)
            validate_database_backup(temporary_path)
            safety_copy = DATABASE_BACKUP_DIR / f"pre_restore_{datetime.now():%Y%m%d_%H%M%S_%f}.db"
            try:
                create_database_snapshot(safety_copy)
            except Exception:
                try:
                    safety_copy.unlink(missing_ok=True)
                except OSError:
                    pass
                raise
            db.close()
            engine.dispose()
            try:
                replace_database_file(temporary_path, DATABASE_PATH)
                init_db()
                audit_db = SessionLocal()
                try:
                    audit_db.add(ActivityLog(event="database_restored", message=f"Database restored from {file.filename}"))
                    audit_db.commit()
                finally:
                    audit_db.close()
            except Exception:
                engine.dispose()
                try:
                    replace_database_file(safety_copy, DATABASE_PATH)
                    init_db()
                except Exception as rollback_error:
                    raise RuntimeError(
                        "Database restore failed and rollback failed; safety copy was retained"
                    ) from rollback_error
                try:
                    safety_copy.unlink(missing_ok=True)
                except OSError:
                    pass
                raise
            clear_sessions()
            _prune_pre_restore_backups(safety_copy)
    except ActiveConfigurationBackupError as exc:
        raise HTTPException(status_code=409, detail="Cannot restore while a configuration backup is running") from exc
    finally:
        if temporary_path:
            temporary_path.unlink(missing_ok=True)
        await file.close()
    return {"ok": True}
