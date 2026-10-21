from collections.abc import Callable
from datetime import datetime, timedelta
import logging
from threading import Lock

from sqlalchemy.orm import Session

from app.models.device import ActivityLog, AppSettings


logger = logging.getLogger(__name__)
_cleanup_lock = Lock()


def prune_activity_log_records(
    session: Session,
    *,
    retention_days: int,
    now: datetime | None = None,
    batch_size: int = 1000,
) -> int:
    cutoff = (now or datetime.utcnow()) - timedelta(days=max(1, retention_days))
    removed = 0
    while True:
        identifiers = [
            row[0]
            for row in (
                session.query(ActivityLog.id)
                .filter(ActivityLog.created_at < cutoff)
                .order_by(ActivityLog.id.asc())
                .limit(max(1, batch_size))
                .all()
            )
        ]
        if not identifiers:
            break
        removed += (
            session.query(ActivityLog)
            .filter(ActivityLog.id.in_(identifiers))
            .delete(synchronize_session=False)
        )
        session.commit()
    return removed


def run_activity_log_cleanup(
    session_factory: Callable[[], Session],
    *,
    now: datetime | None = None,
    wait: bool = False,
) -> int | None:
    if not _cleanup_lock.acquire(blocking=wait):
        return None

    session: Session | None = None
    try:
        session = session_factory()
        settings = session.get(AppSettings, 1)
        retention_days = (settings.log_retention_days if settings else None) or 30
        return prune_activity_log_records(
            session,
            retention_days=retention_days,
            now=now or datetime.utcnow(),
        )
    except Exception:
        if session is not None:
            session.rollback()
        logger.exception("Activity log cleanup failed")
        return None
    finally:
        if session is not None:
            session.close()
        _cleanup_lock.release()
