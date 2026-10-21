import errno
from datetime import datetime
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.device import ActivityLog, InspectionResult, InspectionTask
from app.services.output_files import ensure_available_space, report_directory, reserved_unique_file_path, safe_filename_part
from app.services.settings import get_settings


router = APIRouter()
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
MAX_SCREENSHOT_SIZE = 10 * 1024 * 1024


@router.post("", status_code=status.HTTP_201_CREATED)
async def save_result_screenshot(
    task_id: int = Form(...),
    result_id: int = Form(...),
    image: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    task = db.get(InspectionTask, task_id)
    result = db.query(InspectionResult).filter(
        InspectionResult.id == result_id,
        InspectionResult.task_id == task_id,
    ).first()
    if not task or not result:
        await image.close()
        raise HTTPException(status_code=404, detail="Inspection result not found")
    try:
        content = await image.read(MAX_SCREENSHOT_SIZE + 1)
    finally:
        await image.close()
    if image.content_type != "image/png" or not content.startswith(PNG_SIGNATURE):
        raise HTTPException(status_code=400, detail="Screenshot must be a PNG image")
    if len(content) > MAX_SCREENSHOT_SIZE:
        raise HTTPException(status_code=413, detail="Screenshot is too large")

    task_time = task.created_at or datetime.utcnow()
    filename = (
        f"{safe_filename_part(result.device_name or result.device_ip)}_"
        f"{safe_filename_part(result.device_ip)}_{task_time:%Y%m%d_%H%M%S}.png"
    )
    temporary_path = None
    try:
        directory = report_directory(get_settings(db))
        ensure_available_space(directory, len(content))
        with reserved_unique_file_path(directory, filename) as path:
            temporary_path = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
            temporary_path.write_bytes(content)
            temporary_path.replace(path)
    except OSError as exc:
        if temporary_path:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
        message = "磁盘可用空间不足，无法保存截图" if exc.errno == errno.ENOSPC else "Could not save screenshot"
        db.add(ActivityLog(
            level="ERROR",
            event="result_screenshot_failed",
            device_ip=result.device_ip,
            task_id=task.id,
            message=f"{message}: {exc}",
        ))
        db.commit()
        raise HTTPException(status_code=507 if exc.errno == errno.ENOSPC else 500, detail=message) from exc

    db.add(ActivityLog(
        event="result_screenshot_saved",
        device_ip=result.device_ip,
        task_id=task.id,
        message=f"Screenshot saved: {path}",
    ))
    db.commit()
    return {"filename": path.name, "file_path": str(path)}
