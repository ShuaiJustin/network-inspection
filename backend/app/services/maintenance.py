import asyncio
from contextlib import asynccontextmanager
from threading import Lock
from time import monotonic

from fastapi import HTTPException


class UpdatePreparedError(HTTPException):
    def __init__(self):
        super().__init__(status_code=503, detail="Application update is being installed")


class ActiveConfigurationBackupError(RuntimeError):
    pass


_state_lock = Lock()
_active_database_operations = 0
_active_backup_operations = 0
_restore_pending = False
_restore_active = False
_update_prepared_until = 0.0
UPDATE_PREPARATION_LEASE_SECONDS = 60
_ADMISSION_POLL_SECONDS = 0.01


async def _admit_database_operation(*, backup: bool = False) -> None:
    global _active_database_operations, _active_backup_operations
    while True:
        with _state_lock:
            if _update_prepared_until > monotonic():
                raise UpdatePreparedError
            if not _restore_pending and not _restore_active:
                _active_database_operations += 1
                if backup:
                    _active_backup_operations += 1
                return
        await asyncio.sleep(_ADMISSION_POLL_SECONDS)


def _release_database_operation(*, backup: bool = False) -> None:
    global _active_database_operations, _active_backup_operations
    with _state_lock:
        _active_database_operations -= 1
        if backup:
            _active_backup_operations -= 1


@asynccontextmanager
async def backup_operation():
    await _admit_database_operation(backup=True)
    try:
        yield
    finally:
        _release_database_operation(backup=True)


@asynccontextmanager
async def database_restore_operation():
    global _restore_pending, _restore_active
    while True:
        with _state_lock:
            if _update_prepared_until > monotonic():
                raise UpdatePreparedError
            if not _restore_pending and not _restore_active:
                if _active_backup_operations:
                    raise ActiveConfigurationBackupError
                _restore_pending = True
                break
        await asyncio.sleep(_ADMISSION_POLL_SECONDS)

    try:
        while True:
            with _state_lock:
                if _active_database_operations == 0:
                    _restore_active = True
                    break
            await asyncio.sleep(_ADMISSION_POLL_SECONDS)
        try:
            yield
        finally:
            with _state_lock:
                _restore_active = False
    finally:
        with _state_lock:
            _restore_pending = False


def prepare_update() -> None:
    # Caller holds database_restore_operation, excluding submissions and DB access.
    global _update_prepared_until
    with _state_lock:
        _update_prepared_until = monotonic() + UPDATE_PREPARATION_LEASE_SECONDS


def cancel_update() -> None:
    global _update_prepared_until
    with _state_lock:
        _update_prepared_until = 0.0


@asynccontextmanager
async def task_submission_operation():
    # Task creation is excluded from the middleware, so it registers here.
    async with database_access_operation():
        yield


@asynccontextmanager
async def database_access_operation():
    """Register concurrent DB access while giving restore exclusive admission."""
    await _admit_database_operation()
    try:
        yield
    finally:
        _release_database_operation()
