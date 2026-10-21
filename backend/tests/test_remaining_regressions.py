import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.db import SessionLocal, init_db
from app.models.device import BackupRecord, Device
from app.routers import backup as backup_router
from app.services import raw_outputs
from app.services.collectors import ProtocolResult
from app.services.parsers import parse_ssh_metrics, parse_ssh_output


def test_backup_commit_failure_removes_new_file_without_touching_existing_files(monkeypatch, tmp_path):
    init_db()
    db = SessionLocal()
    try:
        device = Device(name="Backup transaction", ip="198.51.100.240", vendor="Huawei", device_type="switch")
        db.add(device)
        db.commit()
        device_id = device.id

        existing_path = tmp_path / "Huawei" / device.ip / "existing.txt"
        existing_path.parent.mkdir(parents=True)
        existing_path.write_text("existing configuration", encoding="utf-8")
        existing_record = BackupRecord(
            device_id=device_id,
            device_ip=device.ip,
            file_path=str(existing_path),
            status="success",
        )
        db.add(existing_record)
        db.commit()
        existing_record_id = existing_record.id

        monkeypatch.setattr(
            backup_router,
            "get_settings",
            lambda _db: SimpleNamespace(
                backup_directory=str(tmp_path),
                backup_retention_count=1,
                backup_ignore_patterns=[],
                connection_timeout_seconds=10,
                command_timeout_seconds=30,
            ),
        )

        async def fake_backup(*_args, **_kwargs):
            return ProtocolResult(True, "SSH", raw_output="sysname Core-SW\ninterface Vlanif1\nreturn\n")

        monkeypatch.setattr(backup_router, "backup_configuration", fake_backup)

        def fail_commit():
            raise RuntimeError("database commit failed")

        monkeypatch.setattr(db, "commit", fail_commit)
        with pytest.raises(RuntimeError, match="database commit failed"):
            asyncio.run(backup_router._create_backups([device_id], db))

        assert list(tmp_path.rglob("*.txt")) == [existing_path]
        assert existing_path.read_text(encoding="utf-8") == "existing configuration"
        db.expire_all()
        assert db.get(BackupRecord, existing_record_id).file_path == str(existing_path)
    finally:
        db.close()


def test_huawei_hardware_alarm_is_attached_to_the_named_component():
    parsed = parse_ssh_output(
        "Huawei",
        "$ display device\n"
        "5 - PWR Present PowerOn Registered Normal\n"
        "6 - PWR Present PowerOn Registered Normal\n"
        "$ display alarm active\n"
        "1/Independent/2026-08-12/-/0x1/hwPowerRemove/Warning/Start/Power absent (PhysicalName=\"PWR6\")\n",
        {
            "display device": {"power_supplies"},
            "display alarm active": {"active_alarms"},
        },
    )

    components = parsed.metrics["power_supplies"]["components"]
    assert components[0]["status"] == "normal"
    assert components[1]["status"] == "abnormal"
    assert components[1]["alarms"][0]["name"] == "hwPowerRemove"


def test_cisco_show_version_provides_model_and_software_version():
    metrics = parse_ssh_metrics(
        "Cisco",
        "$ show version\n"
        "Cisco IOS XE Software, Version 17.09.04a\n"
        "cisco C9300-24T (X86) processor with 123456K/9876K bytes of memory.\n",
    )

    assert metrics["model"] == "C9300-24T"
    assert metrics["software_version"] == "17.09.04a"


def test_cisco_interface_error_counter_table_is_parsed_from_a_dedicated_command():
    parsed = parse_ssh_output(
        "Cisco",
        "$ show interfaces counters errors\n"
        "Port      Align-Err  FCS-Err  Xmit-Err  Rcv-Err\n"
        "Gi1/0/1  0          3        0         3\n",
        {"show interfaces counters errors": {"interface_errors"}},
    )

    assert parsed.metrics["interface_error_count"] == 3


def test_deleted_task_raw_output_is_discovered_for_retry_on_startup(tmp_path, monkeypatch):
    monkeypatch.setattr(raw_outputs, "RAW_OUTPUT_DIR", tmp_path)
    init_db()
    db = SessionLocal()
    try:
        stored = raw_outputs.store_raw_output(
            tmp_path,
            task_id=987654,
            device_ip="198.51.100.241",
            content="display version\noutput",
            existing_task_bytes=0,
        )

        assert Path(stored.file_path).exists()
        assert raw_outputs.cleanup_orphaned_task_raw_outputs(db) == 1
        assert not Path(stored.file_path).exists()
    finally:
        db.close()
