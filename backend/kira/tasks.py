"""Canonical fire-and-forget background-task helper.

asyncio holds only a WEAK reference to the result of `create_task()`. A task
that nothing else keeps a reference to can be garbage-collected mid-run and
silently cancelled (the "asyncio weakref-GC trap"). `spawn_tracked` keeps a
strong reference in a module-level set until the task finishes, then discards
it and logs (rather than swallows) any exception it raised.

This consolidates the previously-duplicated registries in `kira.database`
(`_BACKGROUND_TASKS` / `_spawn_tracked`) and `kira.api.scans`
(`_MI_ENRICH_TASKS` / `_spawn_mediainfo_enrich`) into one place.
"""

import asyncio
import logging

logger = logging.getLogger(__name__)

# Strong references to in-flight fire-and-forget tasks. Each task is held here
# until its done-callback discards it, so it can't be GC'd mid-run.
_BACKGROUND_TASKS: set[asyncio.Task] = set()


def spawn_tracked(coro, label: str = "") -> asyncio.Task:
    """Schedule `coro` as a tracked fire-and-forget task.

    Holds a strong reference until completion (avoiding the asyncio weakref-GC
    trap), then discards it and logs any exception it raised. Returns the
    created Task.

    Must be called from within a running event loop (uses
    `asyncio.create_task`).
    """
    task = asyncio.create_task(coro)
    _BACKGROUND_TASKS.add(task)

    def _done(t: asyncio.Task) -> None:
        _BACKGROUND_TASKS.discard(t)
        if not t.cancelled() and t.exception() is not None:
            logger.error("background task %r failed: %r", label, t.exception())

    task.add_done_callback(_done)
    return task
