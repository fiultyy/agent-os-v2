"""Tests for Step ② ConsolidatorAgent — episodic → semantic LLM consolidation.

Covers: LLM-driven merge + source archival, P0 provenance filter
(FOREGROUND never touched), and degradation to the deterministic
``service.reflect`` (Jaccard) path on LLM / parse failure.
"""

import os
import sys
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

try:
    import pysqlite3

    sys.modules["sqlite3"] = pysqlite3
    sys.modules["sqlite3.dbapi2"] = pysqlite3.dbapi2
except ImportError:
    pass

import json

import pytest

from src.memory.event_bus import EventType, MemoryEventBus
from src.memory.hooks import (
    ConsolidateContext,
    HookPriority,
    SessionContext,
)
from src.memory.service import MemoryService
from src.memory.sideline.consolidator_agent import (
    ConsolidatorAgent,
    ConsolidatorHook,
    ConsolidatorResult,
)
from src.memory.store import InMemoryStore
from src.memory.types import MemoryFilter, MemoryOrigin, MemoryType


def _svc() -> MemoryService:
    return MemoryService(store=InMemoryStore())


class _MockLLM:
    """Fake LLM client with ``.chat`` (the ConsolidatorAgent interface)."""

    def __init__(self, response: str = "", raises: bool = False) -> None:
        self._response = response
        self._raises = raises

    async def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> str:
        if self._raises:
            raise RuntimeError("LLM boom")
        return self._response


async def _seed_episodic(
    svc: MemoryService,
    agent_id: str,
    contents: list[str],
    origin: MemoryOrigin = MemoryOrigin.AGENT,
) -> list[str]:
    """Store EPISODIC items and return their ids."""
    ids: list[str] = []
    for c in contents:
        ref = await svc.store(
            content=c,
            agent_id=agent_id,
            memory_type=MemoryType.EPISODIC,
            origin=origin,
        )
        ids.append(ref.id)
    return ids


class TestLLMMerge:
    @pytest.mark.asyncio
    async def test_merges_into_semantic_and_archives_sources(self) -> None:
        svc = _svc()
        ids = await _seed_episodic(
            svc, "a", ["今天学了 Rust 的所有权", "Rust 借用检查器很有用"]
        )
        merged_json = json.dumps(
            {
                "merged": [
                    {
                        "summary": "学习 Rust 时,所有权与借用检查器是核心机制,需要重点理解。",
                        "source_ids": ids,
                        "importance": 0.8,
                        "identity_category": "KNOWLEDGE",
                    }
                ],
                "contradictions": [],
            },
            ensure_ascii=False,
        )
        llm = _MockLLM(response=merged_json)
        agent = ConsolidatorAgent(svc, llm)

        r = await agent.consolidate(agent_id="a", trigger="periodic")

        assert r.triggered and r.written and not r.degraded
        assert r.merged_count == 1
        assert len(r.semantic_ids) == 1
        assert set(r.archived_ids) == set(ids)

        # SEMANTIC item written, origin=AGENT, carries identity metadata
        sem = await svc.recall(
            query="", agent_id="a", memory_type=MemoryType.SEMANTIC, top_k=10
        )
        assert len(sem) == 1
        assert sem[0].origin == MemoryOrigin.AGENT
        assert sem[0].metadata.get("identity_category") == "KNOWLEDGE"
        assert sem[0].metadata.get("merged_count") == 2
        assert sem[0].importance == pytest.approx(0.8)

        # source episodics archived → no longer surface in the AGENT-EPISODIC
        # candidate filter (store.search default excludes archived)
        f = MemoryFilter(
            agent_id="a",
            memory_type=MemoryType.EPISODIC,
            origin=MemoryOrigin.AGENT,
        )
        remaining = await svc._store.search(f)
        assert remaining == []

    @pytest.mark.asyncio
    async def test_contradiction_count_recorded(self) -> None:
        svc = _svc()
        ids = await _seed_episodic(svc, "a", ["方案A好", "方案B好", "c", "d"])
        merged_json = json.dumps(
            {
                "merged": [
                    {
                        "summary": "sum",
                        "source_ids": ids[2:],
                        "importance": 0.5,
                        "identity_category": "NONE",
                    }
                ],
                "contradictions": [{"ids": ids[:2], "note": "互斥"}],
            }
        )
        agent = ConsolidatorAgent(svc, _MockLLM(response=merged_json))
        r = await agent.consolidate(agent_id="a")
        assert r.contradiction_count == 1

    @pytest.mark.asyncio
    async def test_single_source_merge_skipped(self) -> None:
        svc = _svc()
        ids = await _seed_episodic(svc, "a", ["only one", "another", "third"])
        # only one source_id → not a real merge → skipped
        merged_json = json.dumps(
            {
                "merged": [
                    {
                        "summary": "solo",
                        "source_ids": [ids[0]],
                        "importance": 0.5,
                        "identity_category": "NONE",
                    }
                ],
                "contradictions": [],
            }
        )
        agent = ConsolidatorAgent(svc, _MockLLM(response=merged_json))
        r = await agent.consolidate(agent_id="a")
        assert r.triggered and not r.written
        assert r.merged_count == 0


class TestP0Provenance:
    @pytest.mark.asyncio
    async def test_foreground_memories_never_merged(self) -> None:
        svc = _svc()
        # Only FOREGROUND episodic exists → P0 filter excludes them
        await _seed_episodic(
            svc, "a", ["user fact 1", "user fact 2"], origin=MemoryOrigin.FOREGROUND
        )
        agent = ConsolidatorAgent(svc, _MockLLM(response="{}"))
        r = await agent.consolidate(agent_id="a")
        assert r.triggered is False  # no candidates
        # FOREGROUND items untouched
        f = MemoryFilter(agent_id="a", memory_type=MemoryType.EPISODIC)
        items = await svc._store.search(f)
        assert len(items) == 2
        assert all(i.origin == MemoryOrigin.FOREGROUND for i in items)
        assert all(not i.archived for i in items)

    @pytest.mark.asyncio
    async def test_mixed_origin_only_agent_consolidated(self) -> None:
        svc = _svc()
        agent_ids = await _seed_episodic(svc, "a", ["agent ep1", "agent ep2"])
        await _seed_episodic(
            svc, "a", ["user ep1"], origin=MemoryOrigin.FOREGROUND
        )
        # LLM only "sees" the two AGENT items — its source_ids must be a
        # subset of the agent-origin ids; FOREGROUND id never appears.
        merged_json = json.dumps(
            {
                "merged": [
                    {
                        "summary": "agent summary",
                        "source_ids": agent_ids,
                        "importance": 0.6,
                        "identity_category": "GOAL",
                    }
                ],
                "contradictions": [],
            }
        )
        agent = ConsolidatorAgent(svc, _MockLLM(response=merged_json))
        r = await agent.consolidate(agent_id="a")
        assert r.written and r.merged_count == 1
        # FOREGROUND item still active & unmodified
        f = MemoryFilter(
            agent_id="a", memory_type=MemoryType.EPISODIC, origin=MemoryOrigin.FOREGROUND
        )
        fg = await svc._store.search(f)
        assert len(fg) == 1 and not fg[0].archived


class TestDegradation:
    @pytest.mark.asyncio
    async def test_llm_failure_degrades_to_reflect(self) -> None:
        svc = _svc()
        # Seed overlapping episodic so reflect's Jaccard path merges them
        await _seed_episodic(
            svc, "a", ["use rust for speed", "rust is fast for speed"]
        )
        agent = ConsolidatorAgent(svc, _MockLLM(raises=True))
        r = await agent.consolidate(agent_id="a", trigger="periodic")
        assert r.degraded is True
        assert r.written  # reflect produced a semantic ref
        assert r.merged_count >= 1
        sem = await svc.recall(
            query="", agent_id="a", memory_type=MemoryType.SEMANTIC, top_k=10
        )
        assert len(sem) >= 1
        assert all(s.origin == MemoryOrigin.AGENT for s in sem)

    @pytest.mark.asyncio
    async def test_bad_json_degrades_to_reflect(self) -> None:
        svc = _svc()
        await _seed_episodic(
            svc, "a", ["alpha beta gamma", "alpha beta gamma delta"]
        )
        agent = ConsolidatorAgent(svc, _MockLLM(response="not json at all"))
        r = await agent.consolidate(agent_id="a")
        assert r.degraded is True
        assert r.merged_count >= 1

    @pytest.mark.asyncio
    async def test_no_candidates_returns_not_triggered(self) -> None:
        svc = _svc()
        agent = ConsolidatorAgent(svc, _MockLLM(response="{}"))
        r = await agent.consolidate(agent_id="ghost")
        assert r.triggered is False
        assert r.merged_count == 0


class TestHook:
    @pytest.mark.asyncio
    async def test_hook_priority_and_dispatch(self) -> None:
        svc = _svc()
        await _seed_episodic(svc, "a", ["x y z", "x y z w"])
        agent = ConsolidatorAgent(svc, _MockLLM(raises=True))  # force degrade path
        hook = ConsolidatorHook(agent)
        assert hook.priority == HookPriority.OBSERVER

        bus = MemoryEventBus()
        bus.register(hook, EventType.CONSOLIDATE)

        ctx = ConsolidateContext(agent_id="a", session_id="s", trigger="periodic")
        result = await bus.emit(EventType.CONSOLIDATE, ctx)
        assert isinstance(result, ConsolidatorResult)
        assert result.degraded and result.written

    @pytest.mark.asyncio
    async def test_session_end_hook_path(self) -> None:
        svc = _svc()
        await _seed_episodic(svc, "a", ["foo bar baz", "foo bar qux"])
        agent = ConsolidatorAgent(svc, _MockLLM(raises=True))
        hook = ConsolidatorHook(agent)

        r = await hook.on_session_end(SessionContext(agent_id="a", session_id="s"))
        assert r.degraded and r.written
