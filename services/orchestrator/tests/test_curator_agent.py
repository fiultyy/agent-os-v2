"""Tests for ④ CuratorAgent — offline LLM curation (archive / merge / correct)."""

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

import pytest

from src.memory.hooks import CurateContext, HookPriority
from src.memory.service import MemoryService
from src.memory.sideline.curator_agent import (
    CurateResult,
    CuratorAgent,
    CuratorHook,
)
from src.memory.store import InMemoryStore
from src.memory.types import (
    MemoryOrigin,
    MemoryScope,
    MemoryState,
    MemoryType,
)


def _svc() -> MemoryService:
    return MemoryService(store=InMemoryStore())


class _MockLLM:
    """Fake LLM client exposing ``.chat`` (the CuratorAgent interface)."""

    def __init__(self, response: str = "", raises: bool = False) -> None:
        self._response = response
        self._raises = raises

    async def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> str:
        if self._raises:
            raise RuntimeError("LLM boom")
        return self._response


async def _seed_agent_memory(
    svc: MemoryService,
    agent_id: str,
    content: str,
    state: MemoryState = MemoryState.ACTIVE,
    importance: float = 0.5,
    memory_type: MemoryType = MemoryType.EPISODIC,
) -> str:
    """Store an origin=AGENT memory and force its lifecycle state."""
    ref = await svc.store(
        content=content,
        agent_id=agent_id,
        memory_type=memory_type,
        scope=MemoryScope.AGENT,
        importance=importance,
        origin=MemoryOrigin.AGENT,
    )
    if state != MemoryState.ACTIVE:
        await svc.update(memory_id=ref.id, state=state)
    return ref.id


# ── candidate collection + scope ───────────────────────────────────


class TestCandidateCollection:
    @pytest.mark.asyncio
    async def test_collects_stale_and_active_agent_memories(self) -> None:
        svc = _svc()
        await _seed_agent_memory(svc, "a", "active one", MemoryState.ACTIVE)
        await _seed_agent_memory(svc, "a", "stale one", MemoryState.STALE)
        # FOREGROUND memory must NOT be a candidate (P0 provenance).
        await svc.store(
            content="user memory",
            agent_id="a",
            origin=MemoryOrigin.FOREGROUND,
        )
        # ARCHIVED must NOT be a candidate.
        arch_id = await _seed_agent_memory(svc, "a", "archived", MemoryState.ACTIVE)
        await svc.update(memory_id=arch_id, archived=True, state=MemoryState.ARCHIVED)

        cur = CuratorAgent(svc, _MockLLM(response="{}"))
        cands = await cur._collect_candidates("a", "all")
        contents = {c["content"] for c in cands}
        assert contents == {"active one", "stale one"}

    @pytest.mark.asyncio
    async def test_scope_semantic_filters_type(self) -> None:
        svc = _svc()
        await _seed_agent_memory(
            svc, "a", "epi", MemoryState.ACTIVE, memory_type=MemoryType.EPISODIC
        )
        await _seed_agent_memory(
            svc, "a", "sem", MemoryState.ACTIVE, memory_type=MemoryType.SEMANTIC
        )
        cur = CuratorAgent(svc, _MockLLM(response="{}"))
        cands = await cur._collect_candidates("a", "semantic")
        assert {c["content"] for c in cands} == {"sem"}

    @pytest.mark.asyncio
    async def test_empty_candidates_short_circuits(self) -> None:
        svc = _svc()
        cur = CuratorAgent(svc, _MockLLM(response='{"archive":["x"]}'))
        r = await cur.curate(agent_id="ghost")
        assert r.triggered and r.scanned == 0
        assert r.archived == []  # LLM never called


# ── plan application: archive / merge / correct ────────────────────


class TestApplyPlan:
    @pytest.mark.asyncio
    async def test_archive_marks_archived(self) -> None:
        svc = _svc()
        mid = await _seed_agent_memory(svc, "a", "redundant", MemoryState.STALE)
        llm = _MockLLM(response=json_dumps({"archive": [mid]}))
        r = await CuratorAgent(svc, llm).curate(agent_id="a")
        assert mid in r.archived
        item = await svc.get(mid)
        assert item is not None and item.archived
        assert item.state == MemoryState.ARCHIVED

    @pytest.mark.asyncio
    async def test_merge_writes_semantic_and_archives_sources(self) -> None:
        svc = _svc()
        s1 = await _seed_agent_memory(svc, "a", "overlap A", MemoryState.ACTIVE)
        s2 = await _seed_agent_memory(svc, "a", "overlap B", MemoryState.ACTIVE)
        plan = {
            "merge": [
                {
                    "source_ids": [s1, s2],
                    "summary": "consolidated insight",
                    "importance": 0.8,
                }
            ]
        }
        llm = _MockLLM(response=json_dumps(plan))
        r = await CuratorAgent(svc, llm).curate(agent_id="a")
        assert len(r.merged) == 1
        merged_item = await svc.get(r.merged[0])
        assert merged_item is not None
        assert merged_item.origin == MemoryOrigin.AGENT
        assert merged_item.memory_type == MemoryType.SEMANTIC
        assert merged_item.content == "consolidated insight"
        assert merged_item.importance == pytest.approx(0.8)
        # sources archived
        for sid in (s1, s2):
            src = await svc.get(sid)
            assert src is not None and src.archived

    @pytest.mark.asyncio
    async def test_correct_updates_content(self) -> None:
        svc = _svc()
        mid = await _seed_agent_memory(svc, "a", "wrong fact 2024", MemoryState.ACTIVE)
        llm = _MockLLM(
            response=json_dumps({"correct": [{"id": mid, "content_fix": "fixed fact 2025"}]})
        )
        r = await CuratorAgent(svc, llm).curate(agent_id="a")
        assert mid in r.corrected
        item = await svc.get(mid)
        assert item is not None and item.content == "fixed fact 2025"

    @pytest.mark.asyncio
    async def test_parse_tolerates_code_fences(self) -> None:
        plan = CuratorAgent._parse(
            '```json\n{"archive": ["a", "b"]}\n```'
        )
        assert plan["archive"] == ["a", "b"]

    @pytest.mark.asyncio
    async def test_parse_extracts_json_amid_prose(self) -> None:
        plan = CuratorAgent._parse(
            'Here is the plan:\n{"archive": ["x"]}\nthanks'
        )
        assert plan["archive"] == ["x"]


# ── degradation (P0 deterministic fallback) ────────────────────────


class TestDegradation:
    @pytest.mark.asyncio
    async def test_llm_timeout_degrades_to_pruner_and_forget(self, monkeypatch) -> None:
        svc = _svc()
        await _seed_agent_memory(svc, "a", "stale item", MemoryState.STALE)

        # Wire deterministic primitives into a fake _state module.
        import types as _types

        fake_state = _types.SimpleNamespace(state_pruner=None, active_forgetting=None)

        class _Pruner:
            async def prune(self, agent_id: str = "") -> Any:
                return _types.SimpleNamespace(archived_ids=["p1"])

        class _Forgetter:
            async def run_sweep(self, agent_id: str = "") -> Any:
                return _types.SimpleNamespace(archived_ids=["f1"])

        fake_state.state_pruner = _Pruner()
        fake_state.active_forgetting = _Forgetter()

        import src.services._state as real_state_mod

        monkeypatch.setattr(real_state_mod, "state_pruner", fake_state.state_pruner)
        monkeypatch.setattr(
            real_state_mod, "active_forgetting", fake_state.active_forgetting
        )

        cur = CuratorAgent(svc, _MockLLM(raises=True))
        r = await cur.curate(agent_id="a")
        assert r.degraded
        assert "p1" in r.archived and "f1" in r.archived

    @pytest.mark.asyncio
    async def test_degrade_without_state_is_noop(self) -> None:
        svc = _svc()
        # Seed a candidate so curate reaches the LLM (None) → degrade.
        await _seed_agent_memory(svc, "a", "a memory", MemoryState.ACTIVE)
        cur = CuratorAgent(svc, None)  # no LLM → ask raises → degrade
        r = await cur.curate(agent_id="a")
        assert r.degraded
        # _state may or may not be wired in the test env; either way no raise.
        assert isinstance(r, CurateResult)


# ── CuratorHook ────────────────────────────────────────────────────


class TestCuratorHook:
    @pytest.mark.asyncio
    async def test_priority_is_observer(self) -> None:
        hook = CuratorHook(CuratorAgent(_svc(), None))
        assert hook.priority == HookPriority.OBSERVER

    @pytest.mark.asyncio
    async def test_on_curate_invokes_curate(self) -> None:
        svc = _svc()
        mid = await _seed_agent_memory(svc, "a", "to archive", MemoryState.STALE)
        llm = _MockLLM(response=json_dumps({"archive": [mid]}))
        hook = CuratorHook(CuratorAgent(svc, llm))
        await hook.on_curate(CurateContext(agent_id="a", scope="all"))
        item = await svc.get(mid)
        assert item is not None and item.archived

    @pytest.mark.asyncio
    async def test_on_curate_swallows_exceptions(self) -> None:
        svc = _svc()

        class _Boom(CuratorAgent):
            async def curate(self, agent_id: str, scope: str = "all", timeout: float = 20.0):
                raise RuntimeError("boom")

        hook = CuratorHook(_Boom(svc, None))
        # Must not raise.
        await hook.on_curate(CurateContext(agent_id="a"))


# ── helpers ────────────────────────────────────────────────────────


def json_dumps(obj: Any) -> str:
    import json

    return json.dumps(obj)
