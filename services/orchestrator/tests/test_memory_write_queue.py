"""Tests for MemoryWriteQueue — W3 bounded-concurrency pool + per-agent
ordering + drain + inline fallback + non-fatal fan-out.

Mirrors the spec §1.1 acceptance criteria:
  ① N agents concurrent ≈ N throughput (not mutually blocked)
  ② same-agent turn-N lands before turn-N+1 (per-agent lock)
  ③ shutdown drains in-flight writes within timeout
  ④ queue failure → inline write (never lost)
  ⑤ one failing write does not block the others
  ⑥ fire(emit)→hook→submit same agent does not deadlock (the core invariant
     the submit/fire split exists to preserve — asyncio.Lock is non-reentrant)
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Patch corrupted miniconda sqlite3 with pysqlite3 before any import that
# transitively touches sqlite3. No-op when pysqlite3 is unavailable.
try:
    import pysqlite3

    sys.modules["sqlite3"] = pysqlite3
    sys.modules["sqlite3.dbapi2"] = pysqlite3.dbapi2
except ImportError:
    pass

import pytest

from src.memory.write_queue import MemoryWriteQueue


class TestPerAgentOrdering:
    @pytest.mark.asyncio
    async def test_same_agent_writes_never_overlap(self) -> None:
        """② per-agent lock: concurrent submits for the SAME agent never run
        their write bodies at the same instant (serialised → ordered)."""
        q = MemoryWriteQueue(concurrency=4)
        active = 0
        max_active = 0

        async def _write():
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0.02)
            active -= 1

        await asyncio.gather(*[q.submit("a1", _write) for _ in range(5)])
        assert max_active == 1  # never overlapped → order preserved


class TestCrossAgentConcurrency:
    @pytest.mark.asyncio
    async def test_different_agents_run_concurrently(self) -> None:
        """① different agents share the semaphore, not the per-agent lock —
        their writes overlap, so throughput scales with agent count."""
        q = MemoryWriteQueue(concurrency=8)
        active = 0
        max_active = 0

        async def _write():
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0.02)
            active -= 1

        await asyncio.gather(*[q.submit(f"a{i}", _write) for i in range(5)])
        assert max_active > 1  # concurrency achieved (not global-serialised)


class TestDrain:
    @pytest.mark.asyncio
    async def test_drain_waits_for_inflight(self) -> None:
        """③ drain blocks until every in-flight (submit + fire) write done."""
        q = MemoryWriteQueue(concurrency=4)
        done = asyncio.Event()

        async def _slow():
            await asyncio.sleep(0.05)
            done.set()

        q.fire("a1", _slow)  # fire-and-forget, tracked in _inflight
        ok = await q.drain(timeout=2.0)
        assert ok is True
        assert done.is_set()

    @pytest.mark.asyncio
    async def test_drain_returns_true_when_empty(self) -> None:
        q = MemoryWriteQueue(concurrency=4)
        assert await q.drain(timeout=1.0) is True


class TestInlineFallback:
    @pytest.mark.asyncio
    async def test_submit_runs_inline_after_shutdown(self) -> None:
        """④ once shutdown, submit runs the write inline — never dropped."""
        q = MemoryWriteQueue(concurrency=4)
        q.shutdown()
        ran = False

        async def _write():
            nonlocal ran
            ran = True

        await q.submit("a1", _write)
        assert ran


class TestNonFatal:
    @pytest.mark.asyncio
    async def test_failing_write_does_not_block_others(self) -> None:
        """⑤ a failing write is logged + swallowed; subsequent writes still run."""
        q = MemoryWriteQueue(concurrency=4)
        landed: list[int] = []

        async def _bad():
            raise RuntimeError("boom")

        async def _ok(n: int):
            landed.append(n)

        await q.submit("a1", _bad)
        await q.submit("a1", lambda: _ok(1))
        assert landed == [1]


class TestFireNoDeadlock:
    @pytest.mark.asyncio
    async def test_fire_then_nested_submit_same_agent_no_deadlock(self) -> None:
        """⑥ fire does NOT take the per-agent lock, so a write_fn that itself
        calls submit for the SAME agent (mirrors chat.py fire(emit)→hook→
        submit) must not deadlock. asyncio.Lock is non-reentrant; this is the
        core W3 invariant the submit/fire split exists to preserve."""
        q = MemoryWriteQueue(concurrency=4)
        nested_done = asyncio.Event()

        async def _inner():
            nested_done.set()

        async def _outer():
            # the outer write_fn submits for the same agent as the fire caller
            await q.submit("a1", _inner)

        q.fire("a1", _outer)
        ok = await q.drain(timeout=2.0)
        assert ok is True
        assert nested_done.is_set()  # would hang/deadlock otherwise
