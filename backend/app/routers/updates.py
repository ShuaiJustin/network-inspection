from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.device import InspectionTask
from app.services.maintenance import (
    ActiveConfigurationBackupError,
    cancel_update,
    database_restore_operation,
    prepare_update,
)


router = APIRouter()


@router.post("/update/prepare")
async def prepare(db: Session = Depends(get_db)):
    try:
        async with database_restore_operation():
            if db.query(InspectionTask.id).filter(InspectionTask.status.in_(("queued", "running"))).first():
                raise HTTPException(status_code=409, detail="Inspection tasks are still active")
            prepare_update()
    except ActiveConfigurationBackupError:
        raise HTTPException(status_code=409, detail="Configuration backup is still active") from None
    return {"ready": True}


@router.post("/update/cancel")
async def cancel():
    cancel_update()
    return {"ready": False}
