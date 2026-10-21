from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.device import ActivityLog
from app.schemas.logs import LogDeleteRequest
from app.services.time import utc_for_api


router = APIRouter()


def _apply_filters(query, level: str | None, keyword: str | None):
    if level:
        query = query.filter(ActivityLog.level == level.strip().upper())
    if keyword and keyword.strip():
        term = keyword.strip()
        query = query.filter(
            or_(
                ActivityLog.event.contains(term),
                ActivityLog.device_ip.contains(term),
                ActivityLog.message.contains(term),
            )
        )
    return query


@router.get("")
def list_logs(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    level: str | None = None,
    keyword: str | None = None,
    db: Session = Depends(get_db),
):
    query = _apply_filters(db.query(ActivityLog), level, keyword)
    total = query.count()
    snapshot_id = query.with_entities(func.max(ActivityLog.id)).scalar() or 0
    records = query.order_by(ActivityLog.id.desc()).offset(offset).limit(limit).all()
    return {"total": total, "snapshot_id": snapshot_id, "items": [
        {
            "id": record.id,
            "level": record.level,
            "event": record.event,
            "device_ip": record.device_ip,
            "task_id": record.task_id,
            "message": record.message,
            "created_at": utc_for_api(record.created_at),
        }
        for record in records
    ]}


@router.delete("")
def delete_logs(payload: LogDeleteRequest, db: Session = Depends(get_db)):
    query = db.query(ActivityLog)
    if payload.ids:
        query = query.filter(ActivityLog.id.in_(payload.ids))
    else:
        query = _apply_filters(query, payload.level, payload.keyword)
        query = query.filter(ActivityLog.id <= payload.before_id)
    deleted = query.delete(synchronize_session=False)
    db.commit()
    return {"deleted": deleted}
