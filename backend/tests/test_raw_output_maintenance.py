import asyncio
from datetime import datetime
import importlib
from types import SimpleNamespace

import pytest


def _maintenance_module():
    try:
        return importlib.import_module("app.services.raw_output_maintenance")
    except ModuleNotFoundError:
        pytest.fail("raw-output maintenance service is missing")


class _FakeSession:
    def __init__(self, retention_days=17):
        self.settings = SimpleNamespace(raw_output_retention_days=retention_days)
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def get(self, _model, _identifier):
        return self.settings

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


def test_cleanup_uses_configured_retention_and_commits(monkeypatch):
    maintenance = _maintenance_module()
    session = _FakeSession(retention_days=17)
    observed = {}
    cleanup_time = datetime(2026, 9, 4, 12, 0, 0)

    def cleanup_expired(current_session, *, retention_days, now):
        observed.update(session=current_session, retention_days=retention_days, now=now)
        return 2

    monkeypatch.setattr(maintenance, "cleanup_expired_raw_outputs", cleanup_expired)
    monkeypatch.setattr(maintenance, "cleanup_orphaned_task_raw_outputs", lambda current: 3)

    removed = maintenance.run_raw_output_cleanup(lambda: session, now=cleanup_time)

    assert removed == 5
    assert observed == {"session": session, "retention_days": 17, "now": cleanup_time}
    assert session.committed is True
    assert session.rolled_back is False
    assert session.closed is True


def test_cleanup_rolls_back_logs_and_returns_none_on_failure(monkeypatch, caplog):
    maintenance = _maintenance_module()
    session = _FakeSession()

    def fail_cleanup(*_args, **_kwargs):
        raise OSError("raw output directory is unavailable")

    monkeypatch.setattr(maintenance, "cleanup_expired_raw_outputs", fail_cleanup)

    with caplog.at_level("ERROR"):
        result = maintenance.run_raw_output_cleanup(lambda: session)

    assert result is None
    assert session.committed is False
    assert session.rolled_back is True
    assert session.closed is True
    assert "raw output directory is unavailable" in caplog.text


def test_cleanup_skips_an_overlapping_run():
    maintenance = _maintenance_module()
    created_sessions = []
    assert maintenance._cleanup_lock.acquire(blocking=False)
    try:
        result = maintenance.run_raw_output_cleanup(
            lambda: created_sessions.append(_FakeSession())
        )
    finally:
        maintenance._cleanup_lock.release()

    assert result is None
    assert created_sessions == []


def test_periodic_cleanup_repeats_and_can_be_cancelled():
    main = importlib.import_module("main")
    calls = []

    async def exercise_loop():
        task = asyncio.create_task(
            main._raw_output_cleanup_loop(
                interval_seconds=0.01,
                cleanup=lambda session_factory: calls.append(session_factory),
                session_factory="test-session-factory",
            )
        )
        for _ in range(50):
            if len(calls) >= 2:
                break
            await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(exercise_loop())

    assert calls[:2] == ["test-session-factory", "test-session-factory"]
