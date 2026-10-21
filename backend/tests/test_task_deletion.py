"""Finished inspection tasks must be removable, and removal must be complete.

The task list only ever grew: every trial run, mistyped name and partially
failed batch stayed forever, and the operator had no way to clear them. Adding
the delete is only half the job -- a task that leaves its results, its stored
raw output files or no audit trail behind is worse than no delete at all.
"""

import asyncio
from datetime import datetime
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import func
import pytest

from app.db import SessionLocal
from app.models.device import ActivityLog, Base, Device, InspectionResult, InspectionTask
from app.db import engine
from app.services import raw_outputs as raw_outputs_service
from app.services.inspection import execute_task
from main import app


client = TestClient(app)


def setup_module():
    Base.metadata.create_all(bind=engine)


def _headers() -> dict[str, str]:
    client.post("/auth/bootstrap", json={"username": "admin", "password": "inspect123"})
    response = client.post("/auth/login", json={"username": "admin", "password": "inspect123"})
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture
def session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _make_task(
    session,
    *,
    status: str,
    name: str = "待删除任务",
    finished: bool | None = None,
) -> InspectionTask:
    if finished is None:
        finished = status in {"completed", "partial_failed", "failed", "cancelled"}
    task = InspectionTask(
        name=name,
        inspection_level="quick",
        collection_mode="snmp",
        status=status,
        total_devices=1,
        completed_devices=1,
        finished_at=datetime.utcnow() if finished else None,
    )
    session.add(task)
    session.commit()
    session.refresh(task)
    return task


def _add_result(session, task: InspectionTask, **overrides) -> InspectionResult:
    result = InspectionResult(
        task_id=task.id,
        device_ip="10.211.0.1",
        device_name="待删除交换机",
        vendor="Huawei",
        collection_source="SNMP",
        connection_status="success",
        authentication_status="success",
        collection_status="success",
        parsing_status="success",
        conclusion="normal",
        **overrides,
    )
    session.add(result)
    session.commit()
    session.refresh(result)
    return result


def test_finished_task_and_its_results_are_removed(session):
    headers = _headers()
    task = _make_task(session, status="completed")
    _add_result(session, task)
    task_id = task.id

    response = client.delete(f"/tasks/{task_id}", headers=headers)

    assert response.status_code == 204
    session.expire_all()
    assert session.get(InspectionTask, task_id) is None
    # A row left pointing at a task that no longer exists would still be counted
    # by any later query over results.
    assert session.query(InspectionResult).filter(InspectionResult.task_id == task_id).count() == 0


def test_deleting_a_task_removes_its_stored_raw_output_files(session, tmp_path, monkeypatch):
    """Raw output lives on disk, so the row alone is not the whole record."""
    headers = _headers()
    monkeypatch.setattr(raw_outputs_service, "RAW_OUTPUT_DIR", tmp_path)
    task = _make_task(session, status="partial_failed")
    stored = raw_outputs_service.store_raw_output(
        tmp_path,
        task_id=task.id,
        device_ip="10.211.0.1",
        content="interface GigabitEthernet0/0/1\n",
        existing_task_bytes=0,
    )
    _add_result(session, task, raw_output_ref=stored.file_path, raw_output_status="complete")
    assert Path(stored.file_path).exists()
    task_id = task.id

    response = client.delete(f"/tasks/{task_id}", headers=headers)

    assert response.status_code == 204
    assert not Path(stored.file_path).exists()
    # The per-task folder must go too, or the directory fills up with empties.
    assert not (tmp_path / f"task_{task_id}").exists()


def test_failed_raw_output_unlink_is_retried_after_the_task_row_is_deleted(
    session, tmp_path, monkeypatch,
):
    headers = _headers()
    monkeypatch.setattr(raw_outputs_service, "RAW_OUTPUT_DIR", tmp_path)
    task = _make_task(session, status="partial_failed")
    stored = raw_outputs_service.store_raw_output(
        tmp_path,
        task_id=task.id,
        device_ip="10.211.0.2",
        content="display version\nlocked output\n",
        existing_task_bytes=0,
    )
    _add_result(session, task, raw_output_ref=stored.file_path, raw_output_status="complete")
    task_id = task.id
    raw_path = Path(stored.file_path)
    target = raw_path.resolve()
    real_unlink = Path.unlink
    failed_once = False

    def fail_first_target_unlink(path, *args, **kwargs):
        nonlocal failed_once
        if path.resolve() == target and not failed_once:
            failed_once = True
            raise PermissionError("file is in use")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_first_target_unlink)
    response = client.delete(f"/tasks/{task_id}", headers=headers)

    assert response.status_code == 204
    session.expire_all()
    assert session.get(InspectionTask, task_id) is None
    assert raw_path.exists()

    monkeypatch.setattr(Path, "unlink", real_unlink)
    assert raw_outputs_service.cleanup_orphaned_task_raw_outputs(session) == 1
    assert not raw_path.exists()
    assert not (tmp_path / f"task_{task_id}").exists()


def test_deleting_a_task_is_written_to_the_audit_log(session):
    headers = _headers()
    task = _make_task(session, status="completed", name="审计任务")
    _add_result(session, task)
    task_id = task.id
    # SQLite hands a deleted task's id to the next one, so an id filter alone
    # would also match audit lines written for earlier, unrelated tasks.
    last_log_id = session.query(func.coalesce(func.max(ActivityLog.id), 0)).scalar()

    client.delete(f"/tasks/{task_id}", headers=headers)

    session.expire_all()
    entry = (
        session.query(ActivityLog)
        .filter(
            ActivityLog.event == "task_deleted",
            ActivityLog.task_id == task_id,
            ActivityLog.id > last_log_id,
        )
        .one()
    )
    # The task row is gone, so the log line is the only remaining trace and has
    # to carry enough to answer "what was removed": name, status and scale.
    assert "审计任务" in (entry.message or "")
    assert "completed" in (entry.message or "")


@pytest.mark.parametrize("status", ["queued", "running"])
def test_a_task_still_running_cannot_be_deleted(session, status):
    """Deleting mid-run would leave the worker writing rows for a dead task."""
    headers = _headers()
    task = _make_task(session, status=status)
    task_id = task.id

    response = client.delete(f"/tasks/{task_id}", headers=headers)

    assert response.status_code == 409
    session.expire_all()
    assert session.get(InspectionTask, task_id) is not None


@pytest.mark.parametrize("status", ["completed", "partial_failed", "failed", "cancelled"])
def test_every_finished_status_can_be_deleted(session, status):
    headers = _headers()
    task = _make_task(session, status=status)
    task_id = task.id

    assert client.delete(f"/tasks/{task_id}", headers=headers).status_code == 204
    session.expire_all()
    assert session.get(InspectionTask, task_id) is None


def test_cancelled_task_cannot_be_deleted_until_worker_finishes(session):
    """Cancelled is a request state; finished_at proves the worker has stopped."""
    headers = _headers()
    task = _make_task(session, status="cancelled", finished=False)
    task_id = task.id

    response = client.delete(f"/tasks/{task_id}", headers=headers)

    assert response.status_code == 409
    session.expire_all()
    assert session.get(InspectionTask, task_id) is not None


def test_worker_finalizes_task_cancelled_before_execution_starts(session):
    task = _make_task(session, status="cancelled", finished=False)
    task.completed_devices = 0
    task.selected_device_snapshot = [{
        "id": 987654,
        "name": "尚未开始采集",
        "ip": "10.211.9.9",
        "vendor": "Huawei",
        "device_type": "switch",
        "group_name": None,
    }]
    session.commit()
    task_id = task.id

    asyncio.run(execute_task(task_id, SessionLocal))

    session.expire_all()
    finalized = session.get(InspectionTask, task_id)
    assert finalized.finished_at is not None
    assert finalized.completed_devices == finalized.total_devices == 1
    assert [result.conclusion for result in finalized.results] == ["cancelled"]


def test_worker_finalizes_legacy_cancelled_task_without_snapshot(session):
    task = _make_task(session, status="cancelled", finished=False)
    task.completed_devices = 0
    task.selected_device_ids = "987655"
    task.selected_device_snapshot = []
    session.commit()
    task_id = task.id

    asyncio.run(execute_task(task_id, SessionLocal))

    session.expire_all()
    finalized = session.get(InspectionTask, task_id)
    assert finalized.finished_at is not None
    assert finalized.completed_devices == finalized.total_devices == 1
    assert [result.conclusion for result in finalized.results] == ["cancelled"]


def test_deleting_an_unknown_task_reports_not_found():
    headers = _headers()

    assert client.delete("/tasks/999999", headers=headers).status_code == 404


def test_deleting_a_task_keeps_the_devices_it_inspected(session):
    """History is disposable; the device inventory behind it is not."""
    headers = _headers()
    session.query(Device).filter(Device.ip == "10.211.0.9").delete(synchronize_session=False)
    session.commit()
    device = Device(
        name="保留设备", ip="10.211.0.9", vendor="Huawei", device_type="switch",
        snmp_version="v2c", snmp_community="x", is_active=True,
    )
    session.add(device)
    session.commit()
    task = _make_task(session, status="completed")
    _add_result(session, task, device_id=device.id)
    task_id, device_id = task.id, device.id

    client.delete(f"/tasks/{task_id}", headers=headers)

    session.expire_all()
    assert session.get(Device, device_id) is not None


def test_deletion_requires_authentication(session):
    task = _make_task(session, status="completed")
    task_id = task.id

    response = client.delete(f"/tasks/{task_id}")

    assert response.status_code == 401
    session.expire_all()
    assert session.get(InspectionTask, task_id) is not None

def test_batch_delete_removes_every_selected_finished_task(session):
    """Clearing a backlog one confirmation at a time is not usable."""
    headers = _headers()
    ids = [_make_task(session, status="completed", name=f"批量-{index}").id for index in range(3)]

    response = client.request("DELETE", "/tasks", headers=headers, json={"task_ids": ids})

    assert response.status_code == 200
    assert response.json()["deleted"] == 3
    session.expire_all()
    assert session.query(InspectionTask).filter(InspectionTask.id.in_(ids)).count() == 0


def test_batch_delete_keeps_running_tasks_and_reports_them(session):
    """A partial batch must say what it skipped instead of failing silently."""
    headers = _headers()
    finished = _make_task(session, status="completed", name="批量-已完成")
    running = _make_task(session, status="running", name="批量-执行中")
    finished_id, running_id = finished.id, running.id

    response = client.request(
        "DELETE", "/tasks", headers=headers, json={"task_ids": [finished_id, running_id]}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["deleted"] == 1
    assert body["skipped"] == [running_id]
    session.expire_all()
    assert session.get(InspectionTask, finished_id) is None
    assert session.get(InspectionTask, running_id) is not None


def test_batch_delete_requires_authentication(session):
    task_id = _make_task(session, status="completed").id

    response = client.request("DELETE", "/tasks", json={"task_ids": [task_id]})

    assert response.status_code == 401
    session.expire_all()
    assert session.get(InspectionTask, task_id) is not None
