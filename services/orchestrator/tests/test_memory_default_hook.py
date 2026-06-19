"""Tests for DefaultMemoryHook — the SYSTEM hook owning memory side-effects.

The compression path (``on_pre_compress``) is exercised without running
the graph, satisfying the P1 acceptance criterion that compression logic
be unit-testable in isolation. All three trigger levels (NONE / ASYNC /
SYNC) are covered.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from src.memory.compressor import AsyncCompressor, ContextMonitor, SyncCompressor
from src.memory.default_hook import DefaultMemoryHook
from src.memory.hooks import CompressContext, SessionContext, TurnContext
from src.memory.migrator import MemoryMigrator
from src.memory.service import MemoryService
from src.memory.store import InMemoryStore
from src.memory.types import MemoryItem, MemoryOrigin, MemoryScope, MemoryType


def _svc() -> MemoryService:
    return MemoryService(store=InMemoryStore())


def _make_hook(svc=None, monitor=None):
    """Build a DefaultMemoryHook with real (in-memory) dependencies."""
    svc = svc or _svc()
    monitor = monitor or ContextMonitor(max_context_tokens=100)
    hook = DefaultMemoryHook(
        memory_service=svc,
        memory_migrator=MemoryMigrator(svc),
        sync_compressor=SyncCompressor(),
        async_compressor=AsyncCompressor(),
        context_monitor=monitor,
    )
    return hook, svc


def _msg(text: str) -> dict:
    return {"role": "user", "content": text}


async def _seed_session_items(svc: MemoryService, n: int = 4) -> None:
    for i in range(n):
        await svc.store(
            content=f"session fact {i} " + "x" * 50,
            agent_id="a1",
            session_id="sess1",
            memory_type=MemoryType.SESSION,
            scope=MemoryScope.AGENT,
            origin=MemoryOrigin.AGENT,
        )


class TestSessionStart:
    @pytest.mark.asyncio
    async def test_creates_session(self) -> None:
        hook, svc = _make_hook()
        await hook.on_session_start(SessionContext("a1", "sess1"))
        # create_session initializes the session; get_recent must not blow up.
        recent = await svc.get_recent("sess1")
        assert isinstance(recent, list)


class TestTurnEnd:
    @pytest.mark.asyncio
    async def test_working_item_migrates_to_session_as_agent(self) -> None:
        hook, svc = _make_hook()
        working = MemoryItem(
            content="User: hi\nAssistant: hello",
            agent_id="a1",
            session_id="sess1",
            memory_type=MemoryType.WORKING,
            scope=MemoryScope.AGENT,
        )
        await hook.on_turn_end(
            TurnContext("a1", "sess1", working_item=working)
        )
        items = await svc.recall(query="", agent_id="a1", session_id="sess1", top_k=10)
        assert any(i.memory_type == MemoryType.SESSION for i in items)
        # P0 invariant preserved: migrated memory is AGENT-origin.
        assert all(i.origin == MemoryOrigin.AGENT for i in items)

    @pytest.mark.asyncio
    async def test_tool_result_item_stored(self) -> None:
        hook, svc = _make_hook()
        tool_item = MemoryItem(
            content="Tool web_search result: rain",
            agent_id="a1",
            session_id="sess1",
            memory_type=MemoryType.WORKING,
            scope=MemoryScope.AGENT,
            metadata={"safety_deadline": True, "tool_result": True},
        )
        await hook.on_turn_end(
            TurnContext("a1", "sess1", tool_result_item=tool_item)
        )
        items = await svc.recall(query="web_search", agent_id="a1", session_id="sess1", top_k=10)
        assert any("web_search" in i.content for i in items)

    @pytest.mark.asyncio
    async def test_conversation_item_stored(self) -> None:
        hook, svc = _make_hook()
        conv = MemoryItem(
            content="User: q\nAssistant: a",
            agent_id="a1",
            session_id="sess1",
            memory_type=MemoryType.SESSION,
            scope=MemoryScope.AGENT,
        )
        await hook.on_turn_end(
            TurnContext("a1", "sess1", conversation_item=conv)
        )
        items = await svc.recall(query="q", agent_id="a1", session_id="sess1", top_k=10)
        assert len(items) >= 1


class TestSessionEnd:
    @pytest.mark.asyncio
    async def test_migrates_session_to_episodic(self) -> None:
        hook, svc = _make_hook()
        # Only AGENT-origin session memories migrate (P0 provenance).
        await svc.store(
            content="some session fact",
            agent_id="a1",
            session_id="sess1",
            memory_type=MemoryType.SESSION,
            scope=MemoryScope.AGENT,
            origin=MemoryOrigin.AGENT,
        )
        await hook.on_session_end(SessionContext("a1", "sess1"))
        episodic = await svc.recall(
            query="", agent_id="a1", memory_type=MemoryType.EPISODIC, top_k=10
        )
        assert len(episodic) >= 1


class TestPreCompress:
    @pytest.mark.asyncio
    async def test_none_when_below_threshold(self) -> None:
        hook, _ = _make_hook()
        ctx = CompressContext(
            agent_id="a1", session_id="sess1", accessor_id="a1",
            messages=[_msg("short message")],  # ~3 tokens << 70
        )
        res = await hook.on_pre_compress(ctx)
        assert res.triggered is False
        assert res.level == "none"

    @pytest.mark.asyncio
    async def test_sync_compresses_and_returns_summary_ids(self) -> None:
        svc = _svc()
        await _seed_session_items(svc, n=4)
        hook, _ = _make_hook(svc=svc, monitor=ContextMonitor(max_context_tokens=100))
        # 100 tokens ≈ 400 chars → ratio 1.0 ≥ sync threshold (0.85)
        ctx = CompressContext(
            agent_id="a1", session_id="sess1", accessor_id="a1",
            messages=[_msg("y" * 400)],
        )
        res = await hook.on_pre_compress(ctx)
        assert res.triggered is True
        assert res.level == "sync"
        assert res.summary_count >= 1
        assert len(res.summary_ids) == res.summary_count
        assert res.original_count >= 3

    @pytest.mark.asyncio
    async def test_async_triggers_and_returns_empty_summary_ids(self) -> None:
        svc = _svc()
        await _seed_session_items(svc, n=4)
        async_comp = AsyncCompressor()
        hook = DefaultMemoryHook(
            memory_service=svc,
            memory_migrator=MemoryMigrator(svc),
            sync_compressor=SyncCompressor(),
            async_compressor=async_comp,
            context_monitor=ContextMonitor(max_context_tokens=100),
        )
        # 75 tokens ≈ 300 chars → ratio 0.75 ∈ [async 0.70, sync 0.85)
        ctx = CompressContext(
            agent_id="a1", session_id="sess1", accessor_id="a1",
            messages=[_msg("w" * 300)],
        )
        res = await hook.on_pre_compress(ctx)
        assert res.triggered is True
        assert res.level == "async"
        assert res.summary_ids == []  # ASYNC fires in the background
        # Drain the background compression so the test leaves nothing pending.
        await async_comp.wait(timeout=5.0)
