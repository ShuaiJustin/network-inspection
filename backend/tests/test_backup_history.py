import asyncio
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models.device import ActivityLog, AppSettings, BackupRecord, Base, Device
from app.routers import backup, settings
from app.schemas.settings import SettingsUpdate
from app.services.collectors import ProtocolResult
from app.services import device_operations


@pytest.fixture
def history_db(tmp_path, monkeypatch):
    database_engine = create_engine("sqlite://")
    Base.metadata.create_all(database_engine)
    root = tmp_path / "original"
    root.mkdir()
    (tmp_path / "next").mkdir()
    calls = 0

    async def collect(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return ProtocolResult(True, "SSH", raw_output=f"sysname History-SW\nvlan {calls}\ninterface Vlanif{calls}\nreturn\n")

    monkeypatch.setattr(backup, "backup_configuration", collect)
    with Session(database_engine) as db:
        db.add(AppSettings(id=1, backup_directory=str(root), backup_retention_count=10))
        db.add(Device(ip="192.0.2.1", name="History-SW", vendor="Huawei", device_type="switch"))
        db.commit()
        yield db
    database_engine.dispose()


def _legacy_record(db, ip, filename, content):
    root = Path(db.get(AppSettings, 1).backup_directory)
    path = root / filename
    path.write_text(content, encoding="utf-8")
    record = BackupRecord(device_id=None, device_ip=ip, file_path=str(path), status="success")
    db.add(record)
    db.commit()
    return record


def test_delete_successful_backup_removes_file_record_and_writes_log(history_db):
    db = history_db
    record = _legacy_record(db, "192.0.2.1", "delete-me.txt", "sysname Delete-Me\n")
    record_id = record.id
    path = Path(record.file_path)

    assert backup.delete_backup(record_id, db) == {"deleted": 1}

    assert not path.exists()
    assert db.get(BackupRecord, record_id) is None
    log = db.query(ActivityLog).filter_by(event="backup_deleted", device_ip="192.0.2.1").one()
    assert log.message == "Configuration backup record deleted"


def test_delete_failed_backup_removes_database_record_without_a_file(history_db):
    db = history_db
    record = BackupRecord(
        device_id=1,
        device_ip="192.0.2.1",
        status="failed",
        message="collection failed",
    )
    db.add(record)
    db.commit()
    record_id = record.id

    assert backup.delete_backup(record_id, db) == {"deleted": 1}
    assert db.get(BackupRecord, record_id) is None


def test_delete_backup_allows_a_missing_file_inside_the_saved_root(history_db):
    db = history_db
    record = _legacy_record(db, "192.0.2.1", "already-gone.txt", "sysname Gone\n")
    record_id = record.id
    Path(record.file_path).unlink()

    assert backup.delete_backup(record_id, db) == {"deleted": 1}
    assert db.get(BackupRecord, record_id) is None


def test_delete_backup_rejects_a_file_outside_the_configured_root(history_db, tmp_path):
    db = history_db
    outside = tmp_path / "outside.txt"
    outside.write_text("must remain", encoding="utf-8")
    record = BackupRecord(
        device_id=1,
        device_ip="192.0.2.1",
        file_path=str(outside),
        status="success",
    )
    db.add(record)
    db.commit()
    record_id = record.id

    with pytest.raises(HTTPException) as caught:
        backup.delete_backup(record_id, db)

    assert caught.value.status_code == 409
    assert db.get(BackupRecord, record_id) is not None
    assert outside.read_text(encoding="utf-8") == "must remain"


def test_delete_backup_uses_the_recorded_root_after_settings_change(history_db, tmp_path):
    db = history_db
    asyncio.run(backup._create_backups([1], db))
    record = db.query(BackupRecord).one()
    record_id = record.id
    path = Path(record.file_path)
    settings.update_settings(SettingsUpdate(backup_directory=str(tmp_path / "next")), db)

    assert backup.delete_backup(record_id, db) == {"deleted": 1}
    assert not path.exists()
    assert db.get(BackupRecord, record_id) is None


def test_delete_backup_keeps_the_record_when_the_file_is_locked(history_db, monkeypatch):
    db = history_db
    record = _legacy_record(db, "192.0.2.1", "locked.txt", "sysname Locked\n")
    record_id = record.id
    path = Path(record.file_path)

    def locked_file(_path, *, missing_ok=False):
        raise PermissionError("file is in use")

    monkeypatch.setattr(Path, "unlink", locked_file)
    with pytest.raises(HTTPException) as caught:
        backup.delete_backup(record_id, db)

    assert caught.value.status_code == 500
    assert db.get(BackupRecord, record_id) is not None
    assert path.exists()


def test_delete_backup_reports_a_missing_record(history_db):
    with pytest.raises(HTTPException) as caught:
        backup.delete_backup(99999, history_db)

    assert caught.value.status_code == 404


def test_detached_backup_compares_only_the_same_ip(history_db):
    db = history_db
    _legacy_record(db, "192.0.2.11", "before.txt", "sysname Same-device-before\n")
    _legacy_record(db, "192.0.2.12", "other.txt", "sysname Unrelated-device\n")
    current = _legacy_record(db, "192.0.2.11", "after.txt", "sysname Same-device-after\n")

    result = backup.compare_backup(current.id, db)

    assert "-sysname Same-device-before" in result["diff"]
    assert "+sysname Same-device-after" in result["diff"]
    assert "Unrelated-device" not in "\n".join(result["diff"])


def test_detached_backup_without_its_own_predecessor_has_no_diff(history_db):
    db = history_db
    _legacy_record(db, "192.0.2.12", "other.txt", "sysname Unrelated-device\n")
    current = _legacy_record(db, "192.0.2.11", "only.txt", "sysname Only-backup\n")

    assert backup.compare_backup(current.id, db) == {
        "diff": [], "message": "No previous valid backup",
    }


def test_new_device_does_not_compare_detached_history_with_the_same_ip(history_db):
    db = history_db
    _legacy_record(db, "192.0.2.1", "detached.txt", "sysname Retired-device\n")
    asyncio.run(backup._create_backups([1], db))
    current = db.query(BackupRecord).filter_by(device_id=1).one()

    assert backup.compare_backup(current.id, db) == {
        "diff": [], "message": "No previous valid backup",
    }


def test_created_backups_keep_their_original_root_after_settings_change(history_db, tmp_path, monkeypatch):
    db = history_db
    original = Path(db.get(AppSettings, 1).backup_directory)
    asyncio.run(backup._create_backups([1], db))
    asyncio.run(backup._create_backups([1], db))
    current_id = db.query(BackupRecord.id).order_by(BackupRecord.id.desc()).first()[0]
    settings.update_settings(SettingsUpdate(backup_directory=str(tmp_path / "next")), db)
    db.expunge_all()

    result = backup.compare_backup(current_id, db)
    current = db.get(BackupRecord, current_id)
    assert current.backup_root == str(original.resolve())
    assert "-vlan 1" in result["diff"]
    assert "+vlan 2" in result["diff"]
    opened = []
    monkeypatch.setattr(backup, "_open_directory", opened.append)
    backup.open_backup_directory(current_id, db)
    assert opened == [Path(current.file_path).parent]


def test_settings_change_captures_the_validated_root_of_legacy_backups(history_db, tmp_path):
    db = history_db
    original = Path(db.get(AppSettings, 1).backup_directory)
    _legacy_record(db, "192.0.2.11", "before.txt", "sysname Before\n")
    current_id = _legacy_record(db, "192.0.2.11", "after.txt", "sysname After\n").id

    settings.update_settings(SettingsUpdate(backup_directory=str(tmp_path / "next")), db)
    db.expunge_all()

    result = backup.compare_backup(current_id, db)
    assert "-sysname Before" in result["diff"]
    assert db.get(BackupRecord, current_id).backup_root == str(original.resolve())


def test_retention_removes_expired_backups_from_their_original_root(history_db, tmp_path):
    db = history_db
    asyncio.run(backup._create_backups([1], db))
    asyncio.run(backup._create_backups([1], db))
    old_records = db.query(BackupRecord).order_by(BackupRecord.id).all()
    first_id, first_path = old_records[0].id, Path(old_records[0].file_path)
    second_path = Path(old_records[1].file_path)
    settings.update_settings(
        SettingsUpdate(backup_directory=str(tmp_path / "next"), backup_retention_count=2), db,
    )

    asyncio.run(backup._create_backups([1], db))
    db.expunge_all()

    assert not first_path.exists()
    assert db.get(BackupRecord, first_id) is None
    assert second_path.exists()
    assert db.query(BackupRecord).count() == 2
    current = db.query(BackupRecord).order_by(BackupRecord.id.desc()).first()
    assert "-vlan 2" in backup.compare_backup(current.id, db)["diff"]


def test_directory_change_does_not_authorize_legacy_paths_outside_the_old_root(history_db, tmp_path):
    db = history_db
    outside = tmp_path / "outside.txt"
    outside.write_text("outside data", encoding="utf-8")
    record = BackupRecord(device_id=1, device_ip="192.0.2.1", file_path=str(outside), status="success")
    db.add(record)
    db.commit()
    record_id = record.id

    settings.update_settings(SettingsUpdate(backup_directory=str(tmp_path / "next")), db)
    db.expunge_all()

    assert db.get(BackupRecord, record_id).backup_root is None
    with pytest.raises(HTTPException) as caught:
        backup.open_backup_directory(record_id, db)
    assert caught.value.status_code == 404
    assert outside.read_text(encoding="utf-8") == "outside data"


def test_record_with_a_saved_root_still_rejects_an_escaped_file(history_db, tmp_path, monkeypatch):
    db = history_db
    asyncio.run(backup._create_backups([1], db))
    record = db.query(BackupRecord).one()
    record_id = record.id
    outside = tmp_path / "outside.txt"
    outside.write_text("outside data", encoding="utf-8")
    record.file_path = str(outside)
    db.commit()
    settings.update_settings(SettingsUpdate(backup_directory=str(tmp_path / "next")), db)
    opened = []
    monkeypatch.setattr(backup, "_open_directory", opened.append)

    with pytest.raises(HTTPException) as caught:
        backup.open_backup_directory(record_id, db)

    assert caught.value.status_code == 404
    assert opened == []
    assert outside.exists()


def test_backup_continues_after_one_device_collector_crashes(history_db, monkeypatch):
    db = history_db
    second = Device(
        ip="192.0.2.2",
        name="History-SW-02",
        vendor="Huawei",
        device_type="switch",
    )
    db.add(second)
    db.commit()

    async def collect(device, *_args, **_kwargs):
        if device.ip == "192.0.2.1":
            raise RuntimeError("password super-secret must not leak")
        return ProtocolResult(
            True,
            "SSH",
            raw_output="sysname History-SW-02\ninterface Vlanif2\nreturn\n",
        )

    monkeypatch.setattr(backup, "backup_configuration", collect)

    response = asyncio.run(backup._create_backups([1, second.id], db))

    assert [record["status"] for record in response["records"]] == ["failed", "success"]
    failed, succeeded = db.query(BackupRecord).order_by(BackupRecord.id).all()
    assert failed.device_ip == "192.0.2.1"
    assert failed.status == "failed"
    assert "super-secret" not in (failed.message or "")
    assert succeeded.device_ip == "192.0.2.2"
    assert Path(succeeded.file_path).is_file()


def test_collector_failure_is_written_to_the_activity_log(history_db, monkeypatch):
    db = history_db

    async def collect(*_args, **_kwargs):
        return ProtocolResult(
            False,
            "SSH",
            failure_stage="collection",
            message="SSH command output decoding failed: invalid start byte",
        )

    monkeypatch.setattr(backup, "backup_configuration", collect)

    asyncio.run(backup._create_backups([1], db))

    record = db.query(BackupRecord).filter_by(status="failed").one()
    log = db.query(ActivityLog).filter_by(
        event="backup_device_failed", device_ip="192.0.2.1",
    ).one()
    assert log.level == "ERROR"
    assert log.message == record.message


@pytest.mark.parametrize("invalid_output", [
    "% Permission denied to execute this command.",
    "sysname History-SW\ninterface Vlanif1\n---- More ----\n",
    "sysname History-SW\ninterface Vlanif1\n",
])
def test_rejected_backup_keeps_previous_success_and_does_not_persist_output(
    history_db, monkeypatch, invalid_output,
):
    db = history_db
    db.get(AppSettings, 1).backup_retention_count = 1
    db.commit()
    asyncio.run(backup._create_backups([1], db))
    previous = db.query(BackupRecord).one()
    previous_id = previous.id
    previous_path = Path(previous.file_path)
    previous_content = previous_path.read_text(encoding="utf-8")
    root = Path(db.get(AppSettings, 1).backup_directory)

    async def collect(*_args, **_kwargs):
        return ProtocolResult(True, "SSH", raw_output=invalid_output)

    monkeypatch.setattr(backup, "backup_configuration", collect)
    response = asyncio.run(backup._create_backups([1], db))

    assert response["records"][0]["status"] == "failed"
    assert response["records"][0]["message"]
    assert previous_path.read_text(encoding="utf-8") == previous_content
    assert db.get(BackupRecord, previous_id).status == "success"
    assert db.query(BackupRecord).filter_by(status="success").count() == 1
    rejected = db.query(BackupRecord).filter_by(status="failed").one()
    assert rejected.file_path is None
    assert sorted(root.rglob("*.txt")) == [previous_path]
    assert list(root.rglob("*.tmp")) == []


def test_failed_success_commit_returns_only_failure_and_keeps_expired_backup(
    history_db, monkeypatch,
):
    db = history_db
    db.get(AppSettings, 1).backup_retention_count = 1
    db.commit()
    asyncio.run(backup._create_backups([1], db))
    previous = db.query(BackupRecord).filter_by(status="success").one()
    previous_id = previous.id
    previous_path = Path(previous.file_path)
    original_commit = db.commit
    commit_calls = 0

    def fail_first_commit():
        nonlocal commit_calls
        commit_calls += 1
        if commit_calls == 1:
            raise RuntimeError("first commit failed")
        original_commit()

    monkeypatch.setattr(db, "commit", fail_first_commit)

    response = asyncio.run(backup._create_backups([1], db))

    assert response["records"] == [{
        "device_ip": "192.0.2.1",
        "status": "failed",
        "message": "Configuration backup failed unexpectedly: RuntimeError: first commit failed",
    }]
    db.expire_all()
    assert db.get(BackupRecord, previous_id).status == "success"
    assert previous_path.is_file()
    assert db.query(BackupRecord).filter_by(status="success").count() == 1
    assert db.query(BackupRecord).filter_by(status="failed").count() == 1
    assert sorted(previous_path.parent.glob("*.txt")) == [previous_path]


def test_backup_reloads_device_after_waiting_for_its_operation_lock(
    history_db, monkeypatch,
):
    observed = []

    async def collect(device, *_args, **_kwargs):
        observed.append((device.name, device.ssh_port))
        return ProtocolResult(False, "SSH", failure_stage="collection", message="expected")

    monkeypatch.setattr(backup, "backup_configuration", collect)

    async def scenario():
        worker = Session(history_db.get_bind())
        try:
            async with device_operations.device_operation_lock(1):
                pending = asyncio.create_task(backup._create_backups([1], worker))
                await asyncio.sleep(0.02)
                with Session(history_db.get_bind()) as updater:
                    device = updater.get(Device, 1)
                    device.name = "Updated-While-Waiting"
                    device.ssh_port = 2222
                    updater.commit()
            await pending
        finally:
            worker.close()

    asyncio.run(scenario())

    assert observed == [("Updated-While-Waiting", 2222)]
