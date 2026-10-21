import asyncio

from app.services.maintenance import (
    backup_operation,
    database_access_operation,
    database_restore_operation,
    task_submission_operation,
)


def test_task_submission_waits_for_restore_in_the_same_event_loop():
    async def scenario():
        restore_entered = asyncio.Event()
        release_restore = asyncio.Event()
        submission_entered = asyncio.Event()

        async def restore():
            async with database_restore_operation():
                restore_entered.set()
                await release_restore.wait()

        async def submit():
            await restore_entered.wait()
            async with task_submission_operation():
                submission_entered.set()

        restore_task = asyncio.create_task(restore())
        submission_task = asyncio.create_task(submit())
        await restore_entered.wait()
        await asyncio.sleep(0.02)
        assert not submission_entered.is_set()
        release_restore.set()
        await asyncio.gather(restore_task, submission_task)

    asyncio.run(scenario())


def test_backup_waits_for_restore_in_the_same_event_loop():
    async def scenario():
        restore_entered = asyncio.Event()
        release_restore = asyncio.Event()
        backup_entered = asyncio.Event()

        async def restore():
            async with database_restore_operation():
                restore_entered.set()
                await release_restore.wait()

        async def backup():
            await restore_entered.wait()
            async with backup_operation():
                backup_entered.set()

        restore_task = asyncio.create_task(restore())
        backup_task = asyncio.create_task(backup())
        await restore_entered.wait()
        await asyncio.sleep(0.02)
        assert not backup_entered.is_set()
        release_restore.set()
        await asyncio.gather(restore_task, backup_task)

    asyncio.run(scenario())


def test_database_access_is_concurrent_and_restore_has_exclusive_admission():
    async def scenario():
        first_entered = asyncio.Event()
        second_entered = asyncio.Event()
        release_active_accesses = asyncio.Event()
        restore_requested = asyncio.Event()
        restore_entered = asyncio.Event()
        release_restore = asyncio.Event()
        late_access_entered = asyncio.Event()

        async def access(entered):
            async with database_access_operation():
                entered.set()
                await release_active_accesses.wait()

        async def restore():
            await first_entered.wait()
            await second_entered.wait()
            restore_requested.set()
            async with database_restore_operation():
                restore_entered.set()
                await release_restore.wait()

        async def late_access():
            await restore_requested.wait()
            await asyncio.sleep(0.02)
            async with database_access_operation():
                late_access_entered.set()

        tasks = [
            asyncio.create_task(access(first_entered)),
            asyncio.create_task(access(second_entered)),
        ]
        try:
            await asyncio.wait_for(first_entered.wait(), timeout=0.5)
            await asyncio.wait_for(second_entered.wait(), timeout=0.5)

            tasks.extend((asyncio.create_task(restore()), asyncio.create_task(late_access())))
            await restore_requested.wait()
            await asyncio.sleep(0.05)
            assert not restore_entered.is_set(), "restore must wait for admitted database access"
            assert not late_access_entered.is_set(), "a pending restore must close admission to later access"

            release_active_accesses.set()
            await asyncio.wait_for(restore_entered.wait(), timeout=0.5)
            assert not late_access_entered.is_set(), "ordinary access must stay blocked during restore"

            release_restore.set()
            await asyncio.wait_for(asyncio.gather(*tasks), timeout=1)
            assert late_access_entered.is_set()
        finally:
            release_active_accesses.set()
            release_restore.set()
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    asyncio.run(scenario())
