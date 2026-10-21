"""A single misbehaving device must never take the whole inspection task down."""

import asyncio

import pytest

import app.services.inspection as inspection
from app.db import SessionLocal, init_db
from app.services import device_operations
from app.models.device import ActivityLog, Device, InspectionResult, InspectionTask


pytestmark = pytest.mark.filterwarnings(
    "error:Object of type <InspectionResult> not in session"
)


@pytest.fixture
def db_session():
    init_db()
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def three_devices(db_session):
    ips = ["10.210.0.%d" % n for n in (1, 2, 3)]
    db_session.query(Device).filter(Device.ip.in_(ips)).delete(synchronize_session=False)
    db_session.commit()
    devices = []
    for ip in ips:
        device = Device(
            name="ISO-" + ip, ip=ip, vendor="Huawei", device_type="switch",
            ssh_port=22, snmp_port=161, snmp_version="v2c",
            snmp_community="x", is_active=True,
        )
        db_session.add(device)
        devices.append(device)
    db_session.commit()
    yield devices


def _make_task(db_session, devices, name):
    task = InspectionTask(
        name=name, status="queued", inspection_level="quick", collection_mode="snmp",
        total_devices=len(devices),
        selected_device_ids=",".join(str(d.id) for d in devices),
    )
    db_session.add(task)
    db_session.commit()
    return task.id


def test_one_crashing_device_does_not_abort_the_task(db_session, three_devices, monkeypatch):
    task_id = _make_task(db_session, three_devices, "crash-isolation")
    boom = three_devices[1].id
    original = inspection._inspect_device

    async def flaky(task, device, settings=None):
        if device.id == boom:
            raise RuntimeError("simulated collector crash")
        return await original(task, device, settings)

    monkeypatch.setattr(inspection, "_inspect_device", flaky)
    asyncio.run(inspection.execute_task(task_id, SessionLocal, max_concurrency=3))

    db_session.expire_all()
    task = db_session.get(InspectionTask, task_id)
    results = db_session.query(InspectionResult).filter_by(task_id=task_id).all()

    assert len(results) == 3, "every device must get a result row"
    assert task.completed_devices == 3
    assert task.status in {"completed", "partial_failed"}
    assert task.finished_at is not None, "task must reach a terminal state"

    crashed = [r for r in results if r.device_id == boom]
    assert crashed and crashed[0].conclusion == "data_fetch_failed"
    assert crashed[0].failure_stage == "collection_error"


def test_the_crash_reason_is_recorded_not_silently_normal(db_session, three_devices, monkeypatch):
    task_id = _make_task(db_session, three_devices, "crash-reason")
    boom = three_devices[0].id
    original = inspection._inspect_device

    async def flaky(task, device, settings=None):
        if device.id == boom:
            raise RuntimeError("collector exploded")
        return await original(task, device, settings)

    monkeypatch.setattr(inspection, "_inspect_device", flaky)
    asyncio.run(inspection.execute_task(task_id, SessionLocal, max_concurrency=2))

    db_session.expire_all()
    crashed = db_session.query(InspectionResult).filter_by(task_id=task_id, device_id=boom).one()
    assert crashed.conclusion != "normal"
    assert crashed.message, "the failure reason must be visible to the operator"

def test_the_task_relationship_still_sees_every_result(db_session, three_devices, monkeypatch):
    """A crash mid-flight must not drop a sibling result from task.results."""
    task_id = _make_task(db_session, three_devices, "relationship-integrity")
    boom = three_devices[1].id
    original = inspection._inspect_device

    async def flaky(task, device, settings=None):
        if device.id == boom:
            raise RuntimeError("boom")
        return await original(task, device, settings)

    monkeypatch.setattr(inspection, "_inspect_device", flaky)
    asyncio.run(inspection.execute_task(task_id, SessionLocal, max_concurrency=3))

    db_session.expire_all()
    task = db_session.get(InspectionTask, task_id)
    rows = db_session.query(InspectionResult).filter_by(task_id=task_id).all()

    assert len(rows) == 3
    assert len(task.results) == 3, "the ORM relationship must match the stored rows"
    assert all(row.task_id == task_id for row in rows)
    assert task.completed_devices == task.success_devices + task.failed_devices


def test_task_level_failure_is_finalized_without_waiting_for_an_app_restart(
    db_session, three_devices, monkeypatch,
):
    task_id = _make_task(db_session, three_devices[:1], "task-level-failure")

    def fail_before_collection(*_args, **_kwargs):
        raise RuntimeError("simulated task setup failure")

    monkeypatch.setattr(inspection, "restore_task_settings", fail_before_collection)

    asyncio.run(inspection.execute_task(task_id, SessionLocal, max_concurrency=1))

    db_session.expire_all()
    task = db_session.get(InspectionTask, task_id)
    assert task.status == "failed"
    assert task.finished_at is not None
    failure_log = db_session.query(ActivityLog).filter_by(
        task_id=task_id,
        event="task_execution_error",
    ).one()
    assert "simulated task setup failure" in failure_log.message


def test_task_level_failure_creates_visible_results_for_unprocessed_devices(
    db_session, three_devices, monkeypatch,
):
    task_id = _make_task(db_session, three_devices[:2], "task-level-visible-results")

    def fail_before_collection(*_args, **_kwargs):
        raise RuntimeError("setup exploded")

    monkeypatch.setattr(inspection, "restore_task_settings", fail_before_collection)

    asyncio.run(inspection.execute_task(task_id, SessionLocal, max_concurrency=1))

    db_session.expire_all()
    task = db_session.get(InspectionTask, task_id)
    results = db_session.query(InspectionResult).filter_by(task_id=task_id).all()

    assert task.status == "failed"
    assert task.completed_devices == 2
    assert task.failed_devices == 2
    assert len(results) == 2
    assert all(result.conclusion == "data_fetch_failed" for result in results)
    assert all(result.failure_stage == "task_execution" for result in results)
    assert all("setup exploded" in (result.message or "") for result in results)


def test_cancelled_task_setup_failure_still_creates_cancelled_results(
    db_session, three_devices, monkeypatch,
):
    task_id = _make_task(db_session, three_devices[:2], "cancelled-task-level-failure")
    task = db_session.get(InspectionTask, task_id)
    task.status = "cancelled"
    task.selected_device_snapshot = [
        {
            "id": device.id,
            "name": device.name,
            "ip": device.ip,
            "vendor": device.vendor,
            "device_type": device.device_type,
        }
        for device in three_devices[:2]
    ]
    db_session.commit()

    def fail_before_collection(*_args, **_kwargs):
        raise RuntimeError("cancelled setup exploded")

    monkeypatch.setattr(inspection, "restore_task_settings", fail_before_collection)

    asyncio.run(inspection.execute_task(task_id, SessionLocal, max_concurrency=1))

    db_session.expire_all()
    task = db_session.get(InspectionTask, task_id)
    results = db_session.query(InspectionResult).filter_by(task_id=task_id).all()
    assert task.status == "cancelled"
    assert task.finished_at is not None
    assert task.completed_devices == task.total_devices == 2
    assert len(results) == 2
    assert all(result.conclusion == "cancelled" for result in results)


def test_unsupported_device_is_completed_but_not_counted_as_failed(
    db_session, three_devices, monkeypatch,
):
    task_id = _make_task(db_session, three_devices[:1], "unsupported-is-not-failed")

    async def unsupported_result(task, device, _settings):
        return InspectionResult(
            task=task,
            device_id=device.id,
            device_ip=device.ip,
            device_name=device.name,
            vendor=device.vendor,
            collection_source="SNMP",
            connection_status="success",
            authentication_status="success",
            collection_status="success",
            parsing_status="success",
            conclusion="unsupported",
            metrics={},
        )

    monkeypatch.setattr(inspection, "inspect_device", unsupported_result)

    asyncio.run(inspection.execute_task(task_id, SessionLocal, max_concurrency=1))

    db_session.expire_all()
    task = db_session.get(InspectionTask, task_id)
    assert task.status == "completed"
    assert task.completed_devices == 1
    assert task.success_devices == 0
    assert task.failed_devices == 0


def test_committed_cancellation_wins_queued_to_running_race(db_session, three_devices):
    task_id = _make_task(db_session, three_devices[:1], "cancel-before-running-update")
    worker_db = SessionLocal()
    control_db = SessionLocal()
    try:
        stale_task = worker_db.get(InspectionTask, task_id)
        cancelled = control_db.get(InspectionTask, task_id)
        cancelled.status = "cancelled"
        control_db.commit()

        current = inspection._start_task_if_queued(worker_db, stale_task)

        assert current.status == "cancelled"
        assert current.started_at is None
        assert worker_db.query(ActivityLog).filter_by(
            task_id=task_id,
            event="task_started",
        ).count() == 0
    finally:
        control_db.close()
        worker_db.close()


def test_committed_cancellation_wins_terminal_status_race(db_session, three_devices):
    task_id = _make_task(db_session, three_devices[:1], "cancel-before-terminal-update")
    task = db_session.get(InspectionTask, task_id)
    task.status = "running"
    db_session.commit()
    worker_db = SessionLocal()
    control_db = SessionLocal()
    try:
        worker_db.get(InspectionTask, task_id)
        cancelled = control_db.get(InspectionTask, task_id)
        cancelled.status = "cancelled"
        control_db.commit()

        current = inspection._set_task_terminal_status(worker_db, task_id, "completed")
        worker_db.commit()

        assert current.status == "cancelled"
        assert current.finished_at is not None
    finally:
        control_db.close()
        worker_db.close()


def test_device_deleted_while_waiting_for_lock_is_never_contacted(
    db_session, three_devices, monkeypatch,
):
    device = three_devices[0]
    task_id = _make_task(db_session, [device], "delete-before-network-connect")
    task = db_session.get(InspectionTask, task_id)
    contacted = []
    snapshot = {
        "id": device.id,
        "name": device.name,
        "ip": device.ip,
        "vendor": device.vendor,
    }

    async def should_not_collect(*_args, **_kwargs):
        contacted.append(True)
        raise AssertionError("deleted device must not be contacted")

    monkeypatch.setattr(inspection, "_inspect_device", should_not_collect)

    async def run_race():
        context_token = inspection._ACTIVE_TASK_DEVICE_CONTEXT.set((SessionLocal, snapshot))
        try:
            async with device_operations.device_operation_lock(device.id):
                worker = asyncio.create_task(inspection.inspect_device(task, device))
                await asyncio.sleep(0)
                delete_db = SessionLocal()
                try:
                    current = delete_db.get(Device, device.id)
                    delete_db.delete(current)
                    delete_db.commit()
                finally:
                    delete_db.close()
            return await asyncio.wait_for(worker, timeout=2)
        finally:
            inspection._ACTIVE_TASK_DEVICE_CONTEXT.reset(context_token)

    result = asyncio.run(run_race())

    assert contacted == []
    assert result.device_id is None
    assert result.failure_stage == "device_deleted"
    assert result.message == "Device was deleted before task execution"
