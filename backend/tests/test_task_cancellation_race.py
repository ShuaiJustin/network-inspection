from datetime import datetime

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models.device import Base, InspectionTask
from app.routers import tasks


def test_stale_session_cannot_cancel_a_task_that_just_finished(tmp_path, monkeypatch):
    database_engine = create_engine(f"sqlite:///{(tmp_path / 'cancel-race.db').as_posix()}")
    Base.metadata.create_all(database_engine)
    with Session(database_engine) as seed:
        task = InspectionTask(name="race", status="queued")
        seed.add(task)
        seed.commit()
        task_id = task.id

    discarded = []
    monkeypatch.setattr(tasks, "discard_task_devices", discarded.append)
    stale_session = Session(database_engine)
    finishing_session = Session(database_engine)
    try:
        assert stale_session.get(InspectionTask, task_id).status == "queued"
        finished = finishing_session.get(InspectionTask, task_id)
        finished.status = "completed"
        finished.finished_at = datetime.utcnow()
        finishing_session.commit()

        with pytest.raises(HTTPException) as caught:
            tasks.cancel_task(task_id, stale_session)

        assert caught.value.status_code == 409
        assert stale_session.get(InspectionTask, task_id).status == "completed"
        assert discarded == []
    finally:
        stale_session.close()
        finishing_session.close()
        database_engine.dispose()
