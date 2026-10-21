import sqlite3

import pytest
from sqlalchemy import create_engine, inspect

from app import db as db_module
from app.models.device import AppSettings, BackupRecord, Base, Device, InspectionResult, InspectionTask


def _legacy_database(path):
    engine = create_engine(f"sqlite:///{path.as_posix()}")
    Base.metadata.create_all(bind=engine)
    with engine.begin() as connection:
        connection.exec_driver_sql("DROP TABLE app_settings")
        connection.exec_driver_sql(
            """
            CREATE TABLE app_settings (
                id INTEGER PRIMARY KEY,
                max_concurrency INTEGER,
                default_collection_mode VARCHAR(20),
                snmp_timeout_seconds INTEGER,
                snmp_retries INTEGER,
                connection_timeout_seconds INTEGER,
                command_timeout_seconds INTEGER,
                network_retry_count INTEGER,
                cpu_warning_threshold INTEGER,
                memory_warning_threshold INTEGER,
                report_directory TEXT,
                backup_directory TEXT,
                log_retention_days INTEGER,
                backup_retention_count INTEGER,
                backup_ignore_patterns JSON,
                storage_warning_threshold INTEGER,
                raw_output_retention_days INTEGER,
                updated_at DATETIME
            )
            """
        )
        connection.exec_driver_sql(
            "INSERT INTO app_settings (id, max_concurrency, cpu_warning_threshold, memory_warning_threshold, storage_warning_threshold) VALUES (1, 10, 80, 80, 80)"
        )
    return engine


def test_schema_migration_creates_a_readable_backup_before_adding_columns(tmp_path):
    database_path = tmp_path / "inspection.db"
    backup_directory = tmp_path / "database_backups"
    engine = _legacy_database(database_path)

    backup_path = db_module.apply_schema_migrations(engine, database_path, backup_directory)

    assert backup_path is not None
    assert backup_path.exists()
    assert "cpu_critical_threshold" in {column["name"] for column in inspect(engine).get_columns("app_settings")}
    with sqlite3.connect(backup_path) as backup_connection:
        backup_columns = {row[1] for row in backup_connection.execute("PRAGMA table_info(app_settings)")}
        assert "cpu_critical_threshold" not in backup_columns
        assert backup_connection.execute("SELECT max_concurrency FROM app_settings WHERE id = 1").fetchone() == (10,)


def test_failed_schema_migration_restores_the_pre_migration_database(tmp_path, monkeypatch):
    database_path = tmp_path / "inspection.db"
    backup_directory = tmp_path / "database_backups"
    engine = _legacy_database(database_path)

    def partially_change_then_fail(database_engine):
        with database_engine.begin() as connection:
            connection.exec_driver_sql("ALTER TABLE app_settings ADD COLUMN cpu_critical_threshold INTEGER DEFAULT 95")
        raise RuntimeError("simulated migration failure")

    monkeypatch.setattr(db_module, "_apply_schema_migration_steps", partially_change_then_fail, raising=False)

    with pytest.raises(RuntimeError, match="restored"):
        db_module.apply_schema_migrations(engine, database_path, backup_directory)

    restored_engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    restored_columns = {column["name"] for column in inspect(restored_engine).get_columns("app_settings")}
    assert "cpu_critical_threshold" not in restored_columns
    assert list(backup_directory.glob("migration_*.db"))


def test_schema_migration_adds_and_backfills_device_platforms(tmp_path):
    database_path = tmp_path / "inspection.db"
    backup_directory = tmp_path / "database_backups"
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    Base.metadata.create_all(bind=engine)
    with engine.begin() as connection:
        connection.exec_driver_sql("DROP TABLE devices")
        connection.exec_driver_sql(
            """
            CREATE TABLE devices (
                id INTEGER PRIMARY KEY,
                ip VARCHAR(45) NOT NULL,
                vendor VARCHAR(20) NOT NULL,
                device_type VARCHAR(20) NOT NULL
            )
            """
        )
        connection.exec_driver_sql(
            "INSERT INTO devices (id, ip, vendor, device_type) VALUES "
            "(1, '192.0.2.1', 'Huawei', 'switch'), "
            "(2, '192.0.2.2', 'Cisco', 'router')"
        )

    db_module.apply_schema_migrations(engine, database_path, backup_directory)

    assert "platform_id" in {column["name"] for column in inspect(engine).get_columns("devices")}
    with engine.connect() as connection:
        rows = connection.exec_driver_sql(
            "SELECT vendor, platform_id FROM devices ORDER BY id"
        ).fetchall()
    assert rows == [("Huawei", "huawei-vrp"), ("Cisco", "cisco-ios")]


def test_schema_migration_rebuilds_device_history_foreign_keys_with_set_null(tmp_path):
    database_path = tmp_path / "inspection.db"
    backup_directory = tmp_path / "database_backups"
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    Base.metadata.create_all(bind=engine)

    with engine.begin() as connection:
        connection.execute(
            Device.__table__.insert(),
            {"id": 1, "ip": "192.0.2.30", "vendor": "Huawei", "device_type": "switch"},
        )
        connection.execute(
            InspectionTask.__table__.insert(),
            {"id": 1, "name": "history", "selected_device_ids": "1"},
        )
        connection.execute(
            InspectionResult.__table__.insert(),
            {
                "id": 1,
                "task_id": 1,
                "device_id": 1,
                "device_ip": "192.0.2.30",
                "connection_status": "success",
                "authentication_status": "success",
                "collection_status": "success",
                "parsing_status": "success",
                "conclusion": "normal",
            },
        )
        connection.execute(
            BackupRecord.__table__.insert(),
            {"id": 1, "device_id": 1, "device_ip": "192.0.2.30", "status": "failed"},
        )

    engine.dispose()
    with sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA foreign_keys=OFF")
        for table_name in ("inspection_results", "backup_records"):
            create_sql = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
                (table_name,),
            ).fetchone()[0]
            legacy_name = f"{table_name}_legacy"
            connection.execute(
                create_sql.replace(
                    f"CREATE TABLE {table_name}",
                    f"CREATE TABLE {legacy_name}",
                    1,
                ).replace(" ON DELETE SET NULL", "")
            )
            connection.execute(f"INSERT INTO {legacy_name} SELECT * FROM {table_name}")
            connection.execute(f"DROP TABLE {table_name}")
            connection.execute(f"ALTER TABLE {legacy_name} RENAME TO {table_name}")
        connection.commit()

    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    backup_path = db_module.apply_schema_migrations(engine, database_path, backup_directory)

    assert backup_path is not None
    with sqlite3.connect(database_path) as connection:
        for table_name in ("inspection_results", "backup_records"):
            foreign_keys = connection.execute(f'PRAGMA foreign_key_list("{table_name}")').fetchall()
            device_key = next(row for row in foreign_keys if row[2] == "devices")
            assert device_key[6].upper() == "SET NULL"
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("DELETE FROM devices WHERE id = 1")
        assert connection.execute("SELECT device_id FROM inspection_results").fetchone() == (None,)
        assert connection.execute("SELECT device_id FROM backup_records").fetchone() == (None,)


def test_migration_captures_only_legacy_backup_paths_under_the_configured_root(tmp_path):
    database_path = tmp_path / "inspection.db"
    root = tmp_path / "trusted"
    root.mkdir()
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            AppSettings.__table__.insert(),
            {"id": 1, "backup_directory": str(root)},
        )
        connection.execute(BackupRecord.__table__.insert(), [
            {"device_ip": "192.0.2.1", "status": "success", "file_path": str(root / "valid.txt")},
            {"device_ip": "192.0.2.2", "status": "success", "file_path": str(tmp_path / "outside.txt")},
        ])
        columns = {column["name"] for column in inspect(connection).get_columns("backup_records")}
        if "backup_root" in columns:
            connection.exec_driver_sql("ALTER TABLE backup_records DROP COLUMN backup_root")
    try:
        saved = db_module.apply_schema_migrations(engine, database_path, tmp_path / "snapshots")
        assert saved is not None
        with engine.connect() as connection:
            rows = connection.exec_driver_sql("SELECT backup_root FROM backup_records ORDER BY id").fetchall()
        assert rows == [(str(root.resolve()),), (None,)]
        assert db_module.apply_schema_migrations(engine, database_path, tmp_path / "snapshots") is None
    finally:
        engine.dispose()
