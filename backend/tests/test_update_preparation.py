import asyncio
from datetime import datetime
import pytest
from fastapi.testclient import TestClient
import main
from app.db import SessionLocal, engine
from app.models.device import Base, InspectionTask
from app.routers.auth import SESSION_LOCK, SESSION_TOKENS, _create_session
from app.services import maintenance

@pytest.fixture
def client(monkeypatch):
    prior_runtime_token = main.app.state.runtime_token
    token = None
    monkeypatch.setattr(maintenance, '_update_prepared_until', 0.0, raising=False)
    try:
        main.configure_runtime_token('runtime')
        Base.metadata.drop_all(bind=engine)
        Base.metadata.create_all(bind=engine)
        token, _ = _create_session(datetime.utcnow())
        with TestClient(main.app) as client:
            client.headers.update({'Authorization': f'Bearer {token}', 'X-Inspection-Run-Token': 'runtime'})
            yield client
    finally:
        main.configure_runtime_token(prior_runtime_token)
        maintenance._update_prepared_until = 0.0
        if token is not None:
            with SESSION_LOCK:
                SESSION_TOKENS.pop(token, None)

@pytest.mark.parametrize('task_status', ['queued', 'running'])
def test_busy_task(client, task_status):
    with SessionLocal() as db:
        db.add(InspectionTask(name='busy', status=task_status))
        db.commit()
    assert client.post('/system/update/prepare').status_code == 409

def test_backup(client):
    maintenance._active_backup_operations += 1
    try:
        assert client.post('/system/update/prepare').status_code == 409
    finally:
        maintenance._active_backup_operations -= 1

def test_auth(client):
    for action in ('prepare', 'cancel'):
        assert client.post(f'/system/update/{action}', headers={'Authorization': 'Bearer wrong'}).status_code == 401
        assert client.post(f'/system/update/{action}', headers={'X-Inspection-Run-Token': 'wrong'}).status_code == 401

def test_freeze_and_cancel(client):
    assert client.post('/system/update/prepare').json() == {'ready': True}
    assert client.post('/tasks', json={'name': 'blocked', 'device_ids': [1]}).status_code == 503
    assert client.get('/devices').status_code == 503
    assert client.post('/system/update/cancel').status_code == 200
    assert client.get('/devices').status_code == 200

def test_lease(client, monkeypatch):
    assert client.post('/system/update/prepare').status_code == 200
    monkeypatch.setattr(maintenance, 'monotonic', lambda: maintenance._update_prepared_until + 1)
    assert client.get('/devices').status_code == 200

def test_restore(client):
    assert client.post('/system/update/prepare').status_code == 200
    async def restore():
        async with maintenance.database_restore_operation():
            pytest.fail('restore admitted')
    with pytest.raises(maintenance.UpdatePreparedError):
        asyncio.run(restore())


def test_prepare_waits_for_submission_and_observes_its_queued_task(client):
    from fastapi import HTTPException
    from app.routers.updates import prepare

    async def scenario():
        entered = asyncio.Event()
        release = asyncio.Event()

        async def submit():
            async with maintenance.task_submission_operation():
                entered.set()
                await release.wait()
                with SessionLocal() as db:
                    db.add(InspectionTask(name='racing submission', status='queued'))
                    db.commit()

        async def prepare_request():
            with SessionLocal() as db:
                return await prepare(db)

        submission = asyncio.create_task(submit())
        await entered.wait()
        preparation = asyncio.create_task(prepare_request())
        await asyncio.sleep(0.03)
        assert not preparation.done()
        release.set()
        await submission
        with pytest.raises(HTTPException) as error:
            await preparation
        assert error.value.status_code == 409
        async with maintenance.task_submission_operation():
            pass

    asyncio.run(scenario())
