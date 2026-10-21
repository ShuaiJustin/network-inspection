import sqlite3

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine

from app.routers import system as system_router
from app.models.device import Base
from app.routers.system import validate_database_backup


REQUIRED_TABLES = ("admin_users", "devices", "inspection_tasks", "inspection_results")


def test_restore_rejects_database_that_fails_sqlite_integrity_check(tmp_path):
    path = tmp_path / "corrupt-index.db"
    connection = sqlite3.connect(path)
    try:
        for table in REQUIRED_TABLES:
            connection.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY, name TEXT)")
        connection.execute("CREATE INDEX ix_devices_name ON devices(name)")
        devices_root = connection.execute(
            "SELECT rootpage FROM sqlite_master WHERE name = 'devices'"
        ).fetchone()[0]
        connection.execute("PRAGMA writable_schema=ON")
        connection.execute(
            "UPDATE sqlite_master SET rootpage = ? WHERE name = 'ix_devices_name'",
            (devices_root,),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(HTTPException) as caught:
        validate_database_backup(path)

    assert caught.value.status_code == 400
    assert "integrity" in str(caught.value.detail).lower()


def test_restore_rejects_named_tables_without_required_core_columns(tmp_path):
    path = tmp_path / "wrong-schema.db"
    connection = sqlite3.connect(path)
    try:
        for table in REQUIRED_TABLES:
            connection.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY)")
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(HTTPException) as caught:
        validate_database_backup(path)

    assert caught.value.status_code == 400
    assert "schema" in str(caught.value.detail).lower()


def test_restore_rejects_a_device_table_missing_runtime_columns(tmp_path):
    path = tmp_path / "incomplete-runtime-schema.db"
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE admin_users (id INTEGER PRIMARY KEY, username TEXT, password_hash TEXT);
            CREATE TABLE devices (
                id INTEGER PRIMARY KEY, ip TEXT, vendor TEXT, device_type TEXT,
                ssh_password TEXT, snmp_community TEXT
            );
            CREATE TABLE inspection_tasks (id INTEGER PRIMARY KEY, name TEXT, status TEXT);
            CREATE TABLE inspection_results (id INTEGER PRIMARY KEY, task_id INTEGER, conclusion TEXT);
            """
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(HTTPException) as caught:
        validate_database_backup(path)

    assert caught.value.status_code == 400
    assert "schema" in str(caught.value.detail).lower()


def test_restore_rejects_a_database_missing_an_application_table(tmp_path):
    path = tmp_path / "missing-application-table.db"
    database_engine = create_engine(f"sqlite:///{path.as_posix()}")
    Base.metadata.create_all(database_engine)
    database_engine.dispose()
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TABLE app_settings")
        connection.commit()

    with pytest.raises(HTTPException) as caught:
        validate_database_backup(path)

    assert caught.value.status_code == 400
    assert "schema" in str(caught.value.detail).lower()


def test_restore_rejects_a_database_missing_an_orm_migratable_column(tmp_path):
    path = tmp_path / "missing-orm-column.db"
    database_engine = create_engine(f"sqlite:///{path.as_posix()}")
    Base.metadata.create_all(database_engine)
    database_engine.dispose()
    with sqlite3.connect(path) as connection:
        connection.execute("ALTER TABLE inspection_tasks DROP COLUMN settings_snapshot")
        connection.commit()

    with pytest.raises(HTTPException) as caught:
        validate_database_backup(path)

    assert caught.value.status_code == 400
    assert "schema" in str(caught.value.detail).lower()


def test_database_replacement_does_not_damage_current_file_when_source_is_invalid(tmp_path):
    current = tmp_path / "current.db"
    incoming = tmp_path / "incoming.db"
    with sqlite3.connect(current) as connection:
        connection.execute("CREATE TABLE marker (value TEXT)")
        connection.execute("INSERT INTO marker VALUES ('current database remains readable')")
    incoming.write_bytes(b"not a SQLite database")

    with pytest.raises(sqlite3.DatabaseError):
        system_router.replace_database_file(incoming, current)

    with sqlite3.connect(current) as connection:
        assert connection.execute("SELECT value FROM marker").fetchone() == (
            "current database remains readable",
        )


def test_restore_accepts_the_previous_schema_without_backup_root(tmp_path):
    path = tmp_path / "previous-version.db"
    database_engine = create_engine(f"sqlite:///{path.as_posix()}")
    Base.metadata.create_all(database_engine)
    with database_engine.begin() as connection:
        columns = {
            row[1] for row in connection.exec_driver_sql("PRAGMA table_info(backup_records)")
        }
        if "backup_root" in columns:
            connection.exec_driver_sql("ALTER TABLE backup_records DROP COLUMN backup_root")
    database_engine.dispose()

    validate_database_backup(path)


@pytest.mark.parametrize(
    "ddl",
    [
        "CREATE TRIGGER forbidden_trigger AFTER INSERT ON devices BEGIN SELECT 1; END",
        "CREATE VIEW forbidden_view AS SELECT id FROM devices",
        "CREATE TABLE forbidden_table (id INTEGER PRIMARY KEY)",
        "CREATE VIRTUAL TABLE forbidden_search USING fts5(content)",
    ],
)
def test_restore_rejects_non_application_schema_objects(tmp_path, ddl):
    path = tmp_path / "unexpected-schema-object.db"
    database_engine = create_engine(f"sqlite:///{path.as_posix()}")
    Base.metadata.create_all(database_engine)
    database_engine.dispose()
    with sqlite3.connect(path) as connection:
        connection.execute(ddl)
        connection.commit()

    with pytest.raises(HTTPException) as caught:
        validate_database_backup(path)

    assert caught.value.status_code == 400
    assert "schema object" in str(caught.value.detail).lower()


@pytest.mark.parametrize(
    "replacement_ddl",
    [
        "CREATE UNIQUE INDEX ix_devices_ip ON devices(ip) WHERE 0",
        "CREATE UNIQUE INDEX ix_devices_ip ON devices(lower(ip))",
    ],
)
def test_restore_rejects_redefined_allowed_device_ip_index(tmp_path, replacement_ddl):
    path = tmp_path / "redefined-device-ip-index.db"
    database_engine = create_engine(f"sqlite:///{path.as_posix()}")
    Base.metadata.create_all(database_engine)
    database_engine.dispose()
    with sqlite3.connect(path) as connection:
        connection.execute("DROP INDEX ix_devices_ip")
        connection.execute(replacement_ddl)
        connection.commit()

    with pytest.raises(HTTPException) as caught:
        validate_database_backup(path)

    assert caught.value.status_code == 400
    assert "schema object" in str(caught.value.detail).lower()


def test_restore_rejects_database_missing_a_required_application_index(tmp_path):
    path = tmp_path / "missing-required-index.db"
    database_engine = create_engine(f"sqlite:///{path.as_posix()}")
    Base.metadata.create_all(database_engine)
    database_engine.dispose()
    with sqlite3.connect(path) as connection:
        connection.execute("DROP INDEX ix_devices_ip")
        connection.commit()

    with pytest.raises(HTTPException) as caught:
        validate_database_backup(path)

    assert caught.value.status_code == 400
    assert "schema" in str(caught.value.detail).lower()
