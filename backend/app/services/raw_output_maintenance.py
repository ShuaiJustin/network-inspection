from collections.abc import Callable
from datetime import datetime
import logging
from threading import Lock

from sqlalchemy.orm import Session

from app.models.device import AppSettings
from app.services.raw_outputs import cleanup_expired_raw_outputs, cleanup_orphaned_task_raw_outputs


logger = logging.getLogger(__name__)
_cleanup_lock = Lock()


def run_raw_output_cleanup(
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
        retention_days = (settings.raw_output_retention_days if settings else None) or 30
        removed = cleanup_expired_raw_outputs(
            session,
            retention_days=retention_days,
            now=now or datetime.utcnow(),
        )
        removed += cleanup_orphaned_task_raw_outputs(session)
        session.commit()
        return removed
    except Exception:
        if session is not None:
            session.rollback()
        logger.exception("Raw output cleanup failed")
        return None
    finally:
        if session is not None:
            session.close()
        _cleanup_lock.release()
