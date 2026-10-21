from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import db as db_module
from app.models.device import Base, Device, InspectionResult, InspectionTask


def test_restart_finalizes_a_cancelled_task_that_was_still_draining(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{(tmp_path / 'recovery.db').as_posix()}")
    Base.metadata.create_all(engine)
    local_session = sessionmaker(bind=engine)
    monkeypatch.setattr(db_module, "SessionLocal", local_session)

    session = local_session()
    task = InspectionTask(
        name="Cancelled during shutdown",
        inspection_level="quick",
        collection_mode="auto",
        status="cancelled",
        total_devices=2,
        completed_devices=1,
        success_devices=1,
        failed_devices=0,
        selected_device_ids="1,2",
        selected_device_snapshot=[
            {"id": 1, "name": "Completed", "ip": "192.0.2.1", "vendor": "Huawei", "device_type": "switch", "group_name": None},
            {"id": 2, "name": "Not started", "ip": "192.0.2.2", "vendor": "Huawei", "device_type": "switch", "group_name": None},
        ],
    )
    session.add(task)
    session.flush()
    session.add(InspectionResult(
        task_id=task.id,
        device_id=1,
        device_ip="192.0.2.1",
        device_name="Completed",
        vendor="Huawei",
        collection_source="SNMP",
        connection_status="success",
        authentication_status="success",
        collection_status="success",
        parsing_status="success",
        conclusion="normal",
        metrics={},
    ))
    session.commit()
    task_id = task.id
    session.close()

    assert db_module.recover_interrupted_tasks() == 1

    session = local_session()
    recovered = session.get(InspectionTask, task_id)
    assert recovered.status == "cancelled"
    assert recovered.finished_at is not None
    assert recovered.completed_devices == recovered.total_devices == 2
    assert recovered.success_devices == 1
    assert recovered.failed_devices == 0
    assert sorted(result.conclusion for result in recovered.results) == ["cancelled", "normal"]
    session.close()


def test_restart_finalizes_legacy_cancelled_task_without_snapshot(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{(tmp_path / 'legacy-cancelled.db').as_posix()}")
    Base.metadata.create_all(engine)
    local_session = sessionmaker(bind=engine)
    monkeypatch.setattr(db_module, "SessionLocal", local_session)

    session = local_session()
    task = InspectionTask(
        name="Legacy cancelled",
        status="cancelled",
        total_devices=2,
        selected_device_ids="101,102",
        selected_device_snapshot=[],
    )
    session.add(task)
    session.commit()
    task_id = task.id
    session.close()

    assert db_module.recover_interrupted_tasks() == 1

    session = local_session()
    recovered = session.get(InspectionTask, task_id)
    assert recovered.finished_at is not None
    assert recovered.completed_devices == recovered.total_devices == 2
    assert len(recovered.results) == 2
    assert all(result.conclusion == "cancelled" for result in recovered.results)
    session.close()


def test_restart_creates_failure_results_for_devices_left_unprocessed(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{(tmp_path / 'interrupted.db').as_posix()}")
    Base.metadata.create_all(engine)
    local_session = sessionmaker(bind=engine)
    monkeypatch.setattr(db_module, "SessionLocal", local_session)

    session = local_session()
    devices = [
        Device(
            name=f"Device {index}",
            ip=f"192.0.2.{index}",
            vendor="Huawei",
            platform_id="huawei-vrp",
            device_type="switch",
            ssh_port=22,
            snmp_port=161,
            snmp_version="v2c",
            snmp_community="encrypted",
            is_active=True,
        )
        for index in (11, 12)
    ]
    session.add_all(devices)
    session.flush()
    snapshots = [
        {
            "id": device.id,
            "name": device.name,
            "ip": device.ip,
            "vendor": device.vendor,
            "platform_id": device.platform_id,
            "device_type": device.device_type,
            "group_name": None,
        }
        for device in devices
    ]
    task = InspectionTask(
        name="Interrupted during collection",
        inspection_level="quick",
        collection_mode="auto",
        status="running",
        total_devices=2,
        completed_devices=1,
        success_devices=1,
        failed_devices=0,
        selected_device_ids=",".join(str(device.id) for device in devices),
        selected_device_snapshot=snapshots,
    )
    session.add(task)
    session.flush()
    session.add(InspectionResult(
        task_id=task.id,
        device_id=devices[0].id,
        device_ip=devices[0].ip,
        device_name=devices[0].name,
        vendor="Huawei",
        collection_source="SNMP",
        connection_status="success",
        authentication_status="success",
        collection_status="success",
        parsing_status="success",
        conclusion="normal",
        metrics={},
    ))
    session.commit()
    task_id = task.id
    second_device_id = devices[1].id
    session.close()

    assert db_module.recover_interrupted_tasks() == 1

    session = local_session()
    recovered = session.get(InspectionTask, task_id)
    assert recovered.status == "failed"
    assert recovered.finished_at is not None
    assert recovered.completed_devices == recovered.total_devices == 2
    assert recovered.success_devices == 1
    assert recovered.failed_devices == 1
    failed = session.query(InspectionResult).filter_by(
        task_id=task_id,
        device_id=second_device_id,
    ).one()
    assert failed.conclusion == "data_fetch_failed"
    assert failed.failure_stage == "task_execution"
    assert "应用退出" in (failed.message or "")
    session.close()
