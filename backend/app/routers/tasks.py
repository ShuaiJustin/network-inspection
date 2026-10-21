from collections import Counter

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload

from app.db import SessionLocal, get_db
from app.models.device import ActivityLog, Device, InspectionResult, InspectionTask
from app.schemas.device import ResultResponse, TaskCreate, TaskDeleteRequest, TaskResponse
from app.services.inspection import execute_task
from app.services.maintenance import task_submission_operation
from app.services.raw_outputs import discard_task_raw_outputs, has_task_raw_outputs, read_raw_output
from app.services.security import redact_configuration_text
from app.services.settings import get_settings, snapshot_task_settings
from app.services.time import utc_for_api
from app.services.task_runtime import capture_task_devices, discard_task_devices


router = APIRouter()

TASK_CONCLUSION_FIELDS = {
    "normal": "normal_devices",
    "warning": "warning_devices",
    "critical": "critical_devices",
    "connection_failed": "connection_failed_devices",
    "authentication_failed": "authentication_failed_devices",
    "data_fetch_failed": "data_fetch_failed_devices",
    "cancelled": "cancelled_devices",
    "unsupported": "unsupported_devices",
    "not_applicable": "not_applicable_devices",
}


def _device_snapshot(device: Device) -> dict:
    return {
        "id": device.id,
        "name": device.name,
        "ip": device.ip,
        "vendor": device.vendor,
        "platform_id": device.platform_id,
        "device_type": device.device_type,
        "group_name": device.group_name,
    }


def _queue_task(
    db: Session,
    background_tasks: BackgroundTasks,
    *,
    name: str,
    inspection_level: str,
    collection_mode: str | None,
    devices: list[Device],
) -> InspectionTask:
    settings = get_settings(db)
    task = InspectionTask(
        name=name,
        inspection_level=inspection_level,
        collection_mode=collection_mode or settings.default_collection_mode,
        status="queued",
        total_devices=len(devices),
    )
    task.selected_device_ids = ",".join(str(device.id) for device in devices)
    task.selected_device_snapshot = [_device_snapshot(device) for device in devices]
    task.settings_snapshot = snapshot_task_settings(settings)
    db.add(task)
    db.commit()
    db.refresh(task)
    capture_task_devices(task.id, devices)
    task_id = task.id
    background_tasks.add_task(lambda: __import__("asyncio").run(execute_task(task_id, SessionLocal)))
    return task


def _retry_task_name(name: str) -> str:
    suffix = "（重试）"
    return f"{name[:120 - len(suffix)]}{suffix}"


def _task_response(task: InspectionTask, counts: Counter | None = None) -> dict:
    counts = counts if counts is not None else Counter(result.conclusion for result in task.results)
    return {
        "id": task.id,
        "name": task.name,
        "inspection_level": task.inspection_level,
        "collection_mode": task.collection_mode,
        "status": task.status,
        "total_devices": task.total_devices,
        "completed_devices": task.completed_devices,
        "success_devices": task.success_devices,
        "failed_devices": task.failed_devices,
        **{field: counts[conclusion] for conclusion, field in TASK_CONCLUSION_FIELDS.items()},
        "created_at": utc_for_api(task.created_at),
        "started_at": utc_for_api(task.started_at),
        "finished_at": utc_for_api(task.finished_at),
    }


def _result_response(result: InspectionResult) -> dict:
    payload = ResultResponse.model_validate(result)
    payload.created_at = utc_for_api(result.created_at)
    return payload.model_dump(mode="json")


@router.get("", response_model=list[TaskResponse])
def list_tasks(db: Session = Depends(get_db)):
    tasks = db.query(InspectionTask).order_by(InspectionTask.id.desc()).all()
    counts_by_task: dict[int, Counter] = {}
    for task_id, conclusion, count in (
        db.query(InspectionResult.task_id, InspectionResult.conclusion, func.count(InspectionResult.id))
        .group_by(InspectionResult.task_id, InspectionResult.conclusion)
        .all()
    ):
        counts_by_task.setdefault(task_id, Counter())[conclusion] = count
    return [_task_response(task, counts_by_task.get(task.id, Counter())) for task in tasks]


@router.post("", response_model=TaskResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_task(payload: TaskCreate, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    async with task_submission_operation():
        devices = db.query(Device).filter(Device.id.in_(payload.device_ids), Device.is_active.is_(True)).all()
        if len(devices) != len(set(payload.device_ids)):
            raise HTTPException(status_code=404, detail="One or more active devices were not found")
        task = _queue_task(
            db,
            background_tasks,
            name=payload.name,
            inspection_level=payload.inspection_level,
            collection_mode=payload.collection_mode,
            devices=devices,
        )
        return _task_response(task)


@router.get("/{task_id}")
def get_task(task_id: int, db: Session = Depends(get_db)):
    task = db.query(InspectionTask).options(selectinload(InspectionTask.results)).filter(InspectionTask.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    body = _task_response(task)
    body["results"] = [_result_response(result) for result in task.results]
    return body


@router.post("/{task_id}/retry", response_model=TaskResponse, status_code=status.HTTP_202_ACCEPTED)
async def retry_task(
    task_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    async with task_submission_operation():
        source = db.get(InspectionTask, task_id)
        if not source:
            raise HTTPException(status_code=404, detail="Task not found")
        if not _task_is_deletable(source):
            raise HTTPException(status_code=409, detail="Only finished tasks can be retried")
        selected_ids = [
            snapshot.get("id")
            for snapshot in (source.selected_device_snapshot or [])
            if isinstance(snapshot.get("id"), int)
        ]
        if not selected_ids:
            selected_ids = [int(value) for value in source.selected_device_ids.split(",") if value]
        selected_ids = list(dict.fromkeys(selected_ids))
        current_devices = db.query(Device).filter(
            Device.id.in_(selected_ids),
            Device.is_active.is_(True),
        ).all()
        current_by_id = {device.id: device for device in current_devices}
        devices = [current_by_id[device_id] for device_id in selected_ids if device_id in current_by_id]
        if not devices:
            raise HTTPException(status_code=409, detail="No active devices from the original task remain")
        task = _queue_task(
            db,
            background_tasks,
            name=_retry_task_name(source.name),
            inspection_level=source.inspection_level,
            collection_mode=source.collection_mode,
            devices=devices,
        )
        return _task_response(task)


@router.get("/{task_id}/results/{result_id}/raw-output")
def read_result_raw_output(task_id: int, result_id: int, db: Session = Depends(get_db)):
    result = db.query(InspectionResult).filter(
        InspectionResult.id == result_id,
        InspectionResult.task_id == task_id,
    ).first()
    if not result:
        raise HTTPException(status_code=404, detail="Inspection result not found")
    if result.raw_output_ref:
        content = read_raw_output(result.raw_output_ref)
        if content is None:
            return {
                "status": "unavailable",
                "byte_count": result.raw_output_bytes,
                "content": None,
            }
        return {
            "status": result.raw_output_status,
            "byte_count": result.raw_output_bytes,
            "content": content,
        }
    return {
        "status": result.raw_output_status,
        "byte_count": result.raw_output_bytes,
        "content": redact_configuration_text(result.raw_output) if result.raw_output else None,
    }


@router.post("/{task_id}/cancel", response_model=TaskResponse)
def cancel_task(task_id: int, db: Session = Depends(get_db)):
    updated = (
        db.query(InspectionTask)
        .filter(
            InspectionTask.id == task_id,
            InspectionTask.status.in_(("queued", "running")),
        )
        .update({InspectionTask.status: "cancelled"}, synchronize_session=False)
    )
    if updated == 0:
        db.rollback()
        db.expire_all()
        task = db.get(InspectionTask, task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        if task.status == "cancelled" and task.finished_at is None:
            discard_task_devices(task_id)
            return _task_response(task)
        raise HTTPException(status_code=409, detail="Finished task cannot be cancelled")
    db.commit()
    discard_task_devices(task_id)
    db.expire_all()
    task = db.get(InspectionTask, task_id)
    return _task_response(task)


# A task may only be removed once nothing is still writing to it.
DELETABLE_TASK_STATUSES = {"completed", "partial_failed", "failed", "cancelled"}


def _task_is_deletable(task: InspectionTask) -> bool:
    # "cancelled" is written as soon as cancellation is requested. finished_at
    # is the durable proof that the worker has drained its in-flight devices.
    return task.status in DELETABLE_TASK_STATUSES and task.finished_at is not None


def _record_task_deletion(db: Session, task: InspectionTask) -> None:
    # The task row is about to disappear, so the log line is the only remaining
    # record of what was removed.
    db.add(ActivityLog(
        event="task_deleted",
        task_id=task.id,
        message=(
            f"Inspection task deleted: {task.name} "
            f"(status={task.status}, devices={task.total_devices}, failed={task.failed_devices})"
        ),
    ))


def _record_raw_output_cleanup_pending(db: Session, task_id: int) -> None:
    if not has_task_raw_outputs(task_id):
        return
    db.add(ActivityLog(
        level="WARNING",
        event="raw_output_cleanup_pending",
        message=f"Raw output cleanup pending for deleted task {task_id}",
    ))
    try:
        db.commit()
    except Exception:
        db.rollback()


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_task(task_id: int, db: Session = Depends(get_db)):
    task = db.get(InspectionTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if not _task_is_deletable(task):
        # The worker still holds this id and would keep writing results for a
        # task that no longer exists, so it has to be cancelled first.
        raise HTTPException(status_code=409, detail="Running task must be cancelled before it can be deleted")
    _record_task_deletion(db, task)
    db.delete(task)
    db.commit()
    # Only once the rows are gone, so a failed commit cannot leave the database
    # pointing at files that were already unlinked.
    discard_task_raw_outputs(task_id)
    _record_raw_output_cleanup_pending(db, task_id)
    return None


@router.delete("", status_code=status.HTTP_200_OK)
def delete_tasks(payload: TaskDeleteRequest, db: Session = Depends(get_db)):
    tasks = db.query(InspectionTask).filter(InspectionTask.id.in_(payload.task_ids)).all()
    found = {task.id for task in tasks}
    deletable = [task for task in tasks if _task_is_deletable(task)]
    # A task that is still running is kept and named, so the caller can say
    # what was left behind instead of silently reporting a smaller count.
    skipped = sorted(found - {task.id for task in deletable})
    deleted_ids = []
    for task in deletable:
        _record_task_deletion(db, task)
        db.delete(task)
        deleted_ids.append(task.id)
    db.commit()
    for task_id in deleted_ids:
        discard_task_raw_outputs(task_id)
        _record_raw_output_cleanup_pending(db, task_id)
    return {
        "deleted": len(deleted_ids),
        "skipped": skipped,
        "missing": sorted(set(payload.task_ids) - found),
    }
