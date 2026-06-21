"""MemoryWriteQueue — bounded concurrency write pool with per-agent ordering.

Backs the W3 write path: multiple agents write concurrently (bounded by a
global Semaphore), while writes from the *same* agent are serialised by a
per-agent ``asyncio.Lock`` so a given agent's turn-N write always lands
before turn-N+1. Drain + inline fallback guarantee no write is lost on
shutdown or queue failure ("lose the async benefit, never the write").

Two entry points (the split avoids a re-entrancy deadlock):

- ``submit(agent_id, write_fn)`` — awaits the write. Used inside
  :class:`~src.memory.default_hook.DefaultMemoryHook` for the per-turn
  store/migrate calls that must preserve ordering. Holds the per-agent lock
  + a semaphore slot.

- ``fire(agent_id, write_fn)`` — fire-and-forget; returns the task without
  awaiting. Used by ``chat.py`` for background work (INGEST / SESSION_END /
  consolidate). Deliberately does NOT take the per-agent lock: those
  write_fns fan out through the event bus to hooks that themselves call
  ``submit`` for the same agent, and taking the lock here would re-enter it
  → deadlock (``asyncio.Lock`` is non-reentrant). Turn-level ordering is
  still guaranteed by the ``await emit(TURN_END)`` calls in chat.py plus
  the hook's ``submit``.

Both register their task in ``_inflight`` so :meth:`drain` can wait on every
in-flight write at shutdown.

Design reference: hermes ``memory_manager.py:575-641`` (drain / inline
fallback / non-fatal fan-out) — reusing the robustness, but correcting the
concurrency model from hermes' single-agent ``max_workers=1`` to a
multi-agent pool + per-agent lock (agent-os-v2 is a multi-agent intake
platform; a single global serialiser would bottleneck).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

WriteFn = Callable[[], Awaitable[Any]]


class MemoryWriteQueue:
    """Bounded-concurrency, per-agent-ordered async write pool."""

    def __init__(self, concurrency: int = 8, drain_timeout: float = 5.0) -> None:
        self._semaphore = asyncio.Semaphore(concurrency)
        self._drain_timeout = drain_timeout
        self._locks: dict[str, asyncio.Lock] = {}
        self._dict_lock = asyncio.Lock()
        self._inflight: set[asyncio.Task[Any]] = set()
        self._shutdown: bool = False

    # ── per-agent lock (lazy, race-free creation) ────────────────────

    async def _get_lock(self, agent_id: str) -> asyncio.Lock:
        lock = self._locks.get(agent_id)
        if lock is not None:
            return lock
        async with self._dict_lock:
            # double-checked: another task may have created it meanwhile
            lock = self._locks.get(agent_id)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[agent_id] = lock
            return lock

    # ── ordered submit (hook-internal writes) ────────────────────────

    async def submit(self, agent_id: str, write_fn: WriteFn) -> Any:
        """Run ``write_fn`` under the per-agent lock + global semaphore.

        Awaits until the write completes, so callers that submit several
        writes for the same agent in sequence preserve landing order. On
        shutdown the write runs inline — losing the concurrency benefit but
        never the write itself. Returns the write's result, or None if it
        failed (non-fatal — the queue logs and swallows).
        """
        if self._shutdown:
            return await self._run_inline(write_fn)
        lock = await self._get_lock(agent_id)
        async with lock:
            async with self._semaphore:
                task = asyncio.create_task(write_fn())
                self._inflight.add(task)
                try:
                    return await task
                except Exception:
                    # non-fatal: one failing write must not block the agent's
                    # subsequent writes or other agents (mirrors the event_bus
                    # non-fatal fan-out contract, event_bus.py:136-145).
                    logger.warning(
                        "memory write for agent=%s failed (non-fatal)",
                        agent_id,
                        exc_info=True,
                    )
                    return None
                finally:
                    self._inflight.discard(task)

    # ── fire-and-forget (chat.py background work) ────────────────────

    def fire(self, agent_id: str, write_fn: WriteFn) -> asyncio.Task[Any]:
        """Schedule ``write_fn`` without awaiting; returns the task.

        Does NOT take the per-agent lock — ``write_fn`` typically fans out
        via the event bus to hooks that ``submit`` for the same agent, and
        re-entering a non-reentrant lock here would deadlock. The task is
        tracked in ``_inflight`` so :meth:`drain` covers it.
        """
        if self._shutdown:
            # Shutdown path: best-effort, untracked (drain won't cover it,
            # but FastAPI has already stopped accepting requests).
            return asyncio.create_task(self._run_inline(write_fn))
        task = asyncio.create_task(self._run_tracked(agent_id, write_fn))
        self._inflight.add(task)
        task.add_done_callback(self._inflight.discard)
        return task

    async def _run_tracked(self, agent_id: str, write_fn: WriteFn) -> None:
        async with self._semaphore:
            try:
                await write_fn()
            except Exception:
                logger.warning(
                    "memory background write for agent=%s failed (non-fatal)",
                    agent_id,
                    exc_info=True,
                )

    @staticmethod
    async def _run_inline(write_fn: WriteFn) -> Any:
        try:
            return await write_fn()
        except Exception:
            logger.warning("memory inline write failed (non-fatal)", exc_info=True)
            return None

    # ── shutdown / drain ─────────────────────────────────────────────

    def shutdown(self) -> None:
        """Stop accepting new pooled writes; subsequent submit/fire run inline."""
        self._shutdown = True

    async def drain(self, timeout: float | None = None) -> bool:
        """Wait for every in-flight write to finish.

        Returns True if all completed within ``timeout`` (default
        ``drain_timeout``), False on timeout. Call :meth:`shutdown` first so
        no new writes enter the pool while draining.
        """
        if not self._inflight:
            return True
        to = self._drain_timeout if timeout is None else timeout
        snapshot = list(self._inflight)
        try:
            await asyncio.wait_for(
                asyncio.gather(*snapshot, return_exceptions=True),
                timeout=to,
            )
            return True
        except asyncio.TimeoutError:
            logger.warning(
                "memory write drain timed out after %.1fs (%d writes pending)",
                to,
                len(self._inflight),
            )
            return False
