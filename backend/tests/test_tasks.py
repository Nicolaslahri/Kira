"""Tests for the canonical fire-and-forget helper `kira.tasks.spawn_tracked`."""

import asyncio
import logging

import pytest

from kira import tasks


@pytest.mark.asyncio
async def test_spawn_tracked_returns_task_and_holds_strong_ref():
    started = asyncio.Event()
    release = asyncio.Event()

    async def work():
        started.set()
        await release.wait()

    task = tasks.spawn_tracked(work(), label="unit")
    assert isinstance(task, asyncio.Task)

    await started.wait()
    # While running, the task is held in the strong-ref registry.
    assert task in tasks._BACKGROUND_TASKS

    release.set()
    await task
    # Done-callback discards it from the registry.
    assert task not in tasks._BACKGROUND_TASKS


@pytest.mark.asyncio
async def test_spawn_tracked_logs_exception_and_discards(caplog):
    async def boom():
        raise ValueError("kaboom")

    with caplog.at_level(logging.ERROR, logger="kira.tasks"):
        task = tasks.spawn_tracked(boom(), label="explode")
        # Let the task run + done-callback fire.
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    assert task not in tasks._BACKGROUND_TASKS
    assert any(
        "background task 'explode' failed" in rec.getMessage()
        and "kaboom" in rec.getMessage()
        for rec in caplog.records
    )


@pytest.mark.asyncio
async def test_spawn_tracked_default_label():
    async def noop():
        return 42

    task = tasks.spawn_tracked(noop())
    result = await task
    assert result == 42
    assert task not in tasks._BACKGROUND_TASKS
