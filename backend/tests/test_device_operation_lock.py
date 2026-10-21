import asyncio
import threading

import pytest

from app.services import device_operations


def test_cancelled_waiter_does_not_leave_the_device_lock_acquired():
    async def scenario():
        device_id = 987654321
        holder_entered = asyncio.Event()
        release_holder = asyncio.Event()

        async def holder():
            async with device_operations.device_operation_lock(device_id):
                holder_entered.set()
                await release_holder.wait()

        async def waiter():
            async with device_operations.device_operation_lock(device_id):
                return True

        holder_task = asyncio.create_task(holder())
        await holder_entered.wait()
        cancelled_waiter = asyncio.create_task(waiter())
        await asyncio.sleep(0.05)
        cancelled_waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cancelled_waiter

        release_holder.set()
        await holder_task
        await asyncio.sleep(0.05)

        assert await asyncio.wait_for(waiter(), timeout=0.5) is True

    asyncio.run(scenario())


def test_completed_operation_removes_its_idle_registry_entry():
    async def scenario():
        device_id = 987654323

        async with device_operations.device_operation_lock(device_id):
            assert device_id in device_operations._device_locks

        assert device_id not in device_operations._device_locks

    asyncio.run(scenario())


def test_waiter_shares_the_registered_lock_until_both_users_finish():
    async def scenario():
        device_id = 987654324
        holder_entered = asyncio.Event()
        release_holder = asyncio.Event()
        waiter_entered = asyncio.Event()

        async def holder():
            async with device_operations.device_operation_lock(device_id):
                holder_entered.set()
                await release_holder.wait()

        async def waiter():
            async with device_operations.device_operation_lock(device_id):
                waiter_entered.set()

        holder_task = asyncio.create_task(holder())
        await holder_entered.wait()
        registered_entry = device_operations._device_locks[device_id]
        waiter_task = asyncio.create_task(waiter())

        for _ in range(50):
            if device_operations._device_locks[device_id].users == 2:
                break
            await asyncio.sleep(0.01)

        assert device_operations._device_locks[device_id] is registered_entry
        assert registered_entry.users == 2
        release_holder.set()
        await asyncio.gather(holder_task, waiter_task)
        assert waiter_entered.is_set()
        assert device_id not in device_operations._device_locks

    asyncio.run(scenario())


def test_cancelled_waiter_releases_lock_even_when_its_event_loop_closes():
    device_id = 987654322
    holder_entered = threading.Event()
    release_holder = threading.Event()

    def hold_lock():
        async def holder_scenario():
            async with device_operations.device_operation_lock(device_id):
                holder_entered.set()
                while not release_holder.is_set():
                    await asyncio.sleep(0.01)

        asyncio.run(holder_scenario())

    holder = threading.Thread(target=hold_lock)
    holder.start()
    assert holder_entered.wait(timeout=1)

    async def scenario():
        async def waiter():
            async with device_operations.device_operation_lock(device_id):
                return True

        waiting = asyncio.create_task(waiter())
        await asyncio.sleep(0.02)
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting

    asyncio.run(scenario())
    release_holder.set()
    holder.join(timeout=1)

    assert not holder.is_alive()
    assert device_id not in device_operations._device_locks
