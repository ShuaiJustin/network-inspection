import asyncio
import threading
from contextlib import asynccontextmanager
from dataclasses import dataclass, field


_registry_lock = threading.Lock()


@dataclass
class _DeviceLockEntry:
    lock: threading.Lock = field(default_factory=threading.Lock)
    users: int = 0


_device_locks: dict[int, _DeviceLockEntry] = {}


def _reserve_device_lock(device_id: int) -> _DeviceLockEntry:
    with _registry_lock:
        entry = _device_locks.setdefault(device_id, _DeviceLockEntry())
        entry.users += 1
        return entry


def _release_device_lock(device_id: int, entry: _DeviceLockEntry) -> None:
    with _registry_lock:
        if _device_locks.get(device_id) is not entry:
            return
        entry.users -= 1
        if entry.users == 0:
            del _device_locks[device_id]


@asynccontextmanager
async def device_operation_lock(device_id: int):
    entry = _reserve_device_lock(device_id)
    acquired = False
    try:
        while not entry.lock.acquire(blocking=False):
            await asyncio.sleep(0.01)
        acquired = True
        yield
    finally:
        if acquired:
            entry.lock.release()
        _release_device_lock(device_id, entry)
