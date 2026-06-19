"""Tests for P3 TaskConsolidationAgent — task-post online consolidation."""

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

from src.memory.service import MemoryService
from src.memory.store import InMemoryStore
from src.memory.sideline.task_consolidator import TaskConsolidationAgent
from src.memory.types import MemoryOrigin, MemoryType


def _svc() -> MemoryService:
    return MemoryService(store=InMemoryStore())


class _MockLLM:
    """Fake LLM client with .chat (the TaskConsolidator interface)."""

    def __init__(self, response: str = "", raises: bool = False) -> None:
        self._response = response
        self._raises = raises

    async def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> str:
        if self._raises:
            raise RuntimeError("LLM boom")
        return self._response


class TestExtractAndWriteBack:
    @pytest.mark.asyncio
    async def test_high_confidence_fast_channel(self) -> None:
        svc = _svc()
        llm = _MockLLM(response="CONFIDENCE: 0.9\nCONTENT:\n关键决策:选用 Rust 重写核心模块")
        tc = TaskConsolidationAgent(svc, llm)
        msgs = [{"role": "user", "content": "q"}, {"role": "assistant", "content": "a"}]
        r = await tc.consolidate_task(agent_id="a", session_id="s", messages=msgs)
        assert r.triggered and r.written
        assert r.confidence == 0.9
        assert r.channel == "fast"  # ≥0.8 → FAST (working memory)
        items = await svc.recall(query="", agent_id="a", session_id="s", top_k=10)
        assert any("Rust" in i.content for i in items)
        assert all(i.origin == MemoryOrigin.AGENT for i in items)

    @pytest.mark.asyncio
    async def test_medium_confidence_semantic(self) -> None:
        svc = _svc()
        llm = _MockLLM(response="CONFIDENCE: 0.6\nCONTENT:\n工具模式:用 web_search 查最新信息")
        tc = TaskConsolidationAgent(svc, llm)
        r = await tc.consolidate_task(
            agent_id="a", session_id="s",
            messages=[{"role": "assistant", "content": "x"}],
        )
        assert r.channel == "medium"
        items = await svc.recall(query="", agent_id="a", memory_type=MemoryType.SEMANTIC, top_k=10)
        assert len(items) >= 1


class TestDegradation:
    @pytest.mark.asyncio
    async def test_llm_failure_degrades_to_episodic(self) -> None:
        svc = _svc()
        llm = _MockLLM(raises=True)
        tc = TaskConsolidationAgent(svc, llm)
        r = await tc.consolidate_task(
            agent_id="a", session_id="s",
            messages=[{"role": "assistant", "content": "did something useful"}],
        )
        assert r.degraded
        assert r.written
        items = await svc.recall(query="", agent_id="a", memory_type=MemoryType.EPISODIC, top_k=10)
        assert len(items) >= 1
        assert all(i.origin == MemoryOrigin.AGENT for i in items)

    @pytest.mark.asyncio
    async def test_no_llm_heuristic_does_not_crash(self) -> None:
        svc = _svc()
        tc = TaskConsolidationAgent(svc, None)
        r = await tc.consolidate_task(
            agent_id="a", session_id="s",
            messages=[{"role": "assistant", "content": "hello world"}],
        )
        assert r.triggered
        assert r.confidence == 0.4  # heuristic
        # confidence 0.4 → SLOW channel; BackwardWriter slow needs LLM
        # (agenerate) which is absent → writer reports written=False gracefully
        assert r.channel == "slow"

    @pytest.mark.asyncio
    async def test_empty_messages_does_not_crash(self) -> None:
        svc = _svc()
        tc = TaskConsolidationAgent(svc, _MockLLM())
        r = await tc.consolidate_task(agent_id="a", session_id="s", messages=[])
        assert r.triggered  # never raises
