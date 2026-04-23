"""P3-4: KGWriteLock — per-branch write lock for Knowledge Graph mutations.

When multiple branches operate in parallel, each needs exclusive
write access to its own KG partition.  KGWriteLock provides a
lightweight async-compatible locking mechanism keyed by branch_id.

Uses threading.Lock for sync callers and asyncio.Lock for async callers.
Thread-safe and safe for single-process multi-async-task usage.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


class KGWriteLock:
    """Per-branch write lock for KG mutations.

    Usage::

        lock_manager = KGWriteLock()

        # Sync context
        with lock_manager.sync_lock("branch-1"):
            kg.add_entity(...)

        # Async context
        async with lock_manager.async_lock("branch-1"):
            await kg.add_entity(...)

        # Convenience wrapper
        result = lock_manager.execute_with_lock("branch-1", my_fn, arg1, arg2)
    """

    def __init__(self) -> None:
        self._sync_locks: dict[str, threading.Lock] = {}
        self._async_locks: dict[str, asyncio.Lock] = {}
        self._meta_lock = threading.Lock()  # protects dict mutations

    # ── Sync lock ───────────────────────────────────────────────────

    def acquire(self, branch_id: str) -> threading.Lock:
        """Get or create a sync Lock for the given branch.

        Returns the Lock (caller must release manually).
        Prefer using ``sync_lock()`` context manager.
        """
        with self._meta_lock:
            if branch_id not in self._sync_locks:
                self._sync_locks[branch_id] = threading.Lock()
            lock = self._sync_locks[branch_id]
        lock.acquire()
        return lock

    def release(self, branch_id: str) -> None:
        """Release a previously acquired lock for the branch."""
        lock = self._sync_locks.get(branch_id)
        if lock and lock.locked():
            lock.release()

    def sync_lock(self, branch_id: str):
        """Context manager for synchronous KG write operations.

        Usage::

            with lock_manager.sync_lock("branch-1"):
                kg.write(...)
        """
        return _SyncLockContext(self, branch_id)

    # ── Async lock ──────────────────────────────────────────────────

    def async_lock(self, branch_id: str):
        """Context manager for async KG write operations.

        Usage::

            async with lock_manager.async_lock("branch-1"):
                await kg.write(...)
        """
        return _AsyncLockContext(self, branch_id)

    def _get_async_lock(self, branch_id: str) -> asyncio.Lock:
        """Get or create an asyncio Lock for the branch."""
        with self._meta_lock:
            if branch_id not in self._async_locks:
                self._async_locks[branch_id] = asyncio.Lock()
            return self._async_locks[branch_id]

    # ── Convenience ─────────────────────────────────────────────────

    def execute_with_lock(
        self,
        branch_id: str,
        fn: Callable,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """Execute a function under the branch's write lock (sync).

        Args:
            branch_id: Branch to lock.
            fn: Callable to execute.
            *args, **kwargs: Passed through to fn.

        Returns:
            Whatever fn returns.
        """
        with self.sync_lock(branch_id):
            return fn(*args, **kwargs)

    # ── Cleanup ─────────────────────────────────────────────────────

    def cleanup(self, branch_id: str) -> None:
        """Remove lock entries for a pruned/archived branch."""
        with self._meta_lock:
            self._sync_locks.pop(branch_id, None)
            self._async_locks.pop(branch_id, None)

    def active_branches(self) -> list[str]:
        """Return branch IDs that have locks (may or may not be held)."""
        with self._meta_lock:
            return list(set(self._sync_locks.keys()) | set(self._async_locks.keys()))


class _SyncLockContext:
    """Context manager wrapper for sync threading.Lock."""

    def __init__(self, manager: KGWriteLock, branch_id: str) -> None:
        self._manager = manager
        self._branch_id = branch_id
        self._lock: Optional[threading.Lock] = None

    def __enter__(self) -> "KGWriteLock":
        self._lock = self._manager.acquire(self._branch_id)
        return self._manager

    def __exit__(self, *exc: Any) -> None:
        self._manager.release(self._branch_id)


class _AsyncLockContext:
    """Context manager wrapper for asyncio.Lock."""

    def __init__(self, manager: KGWriteLock, branch_id: str) -> None:
        self._manager = manager
        self._branch_id = branch_id
        self._lock: Optional[asyncio.Lock] = None

    async def __aenter__(self) -> "KGWriteLock":
        self._lock = self._manager._get_async_lock(self._branch_id)
        await self._lock.acquire()
        return self._manager

    async def __aexit__(self, *exc: Any) -> None:
        if self._lock and self._lock.locked():
            self._lock.release()
