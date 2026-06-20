"""Tests for ① IngestorAgent — LLM semantic ingestion side agent."""

import os
import sys
import tempfile
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

try:
    import pysqlite3

    sys.modules["sqlite3"] = pysqlite3
    sys.modules["sqlite3.dbapi2"] = pysqlite3.dbapi2
except ImportError:
    pass

import pytest

from src.memory.knowledge_graph import KnowledgeGraph
from src.memory.service import MemoryService
from src.memory.sideline.ingestor_agent import IngestorAgent, IngestorHook
from src.memory.store import InMemoryStore
from src.memory.types import MemoryOrigin, MemoryType


def _kg() -> KnowledgeGraph:
    """Fresh on-disk KG in a temp file (isolated per test)."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    return KnowledgeGraph(db_path=path)


def _svc() -> MemoryService:
    return MemoryService(store=InMemoryStore())


class _MockLLM:
    """Fake LLM client exposing the unified .chat interface."""

    def __init__(self, response: str = "", raises: bool = False, delay: float = 0.0) -> None:
        self._response = response
        self._raises = raises
        self._delay = delay
        self.calls: list[dict[str, Any]] = []

    async def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> str:
        import asyncio

        self.calls.append({"messages": messages, "kwargs": kwargs})
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._raises:
            raise RuntimeError("LLM boom")
        return self._response


_VALID_JSON = (
    '{"entities": ['
    '{"name": "Claude", "type": "identity", "properties": {"role": "assistant"}}, '
    '{"name": "deploy_service", "type": "capability", "properties": {}}'
    '], "relations": ['
    '{"subject": "Claude", "predicate": "can_do", "object": "deploy_service", "weight": 0.8}'
    '], "importance": {'
    '"recency": 0.9, "frequency": 0.5, "relevance": 0.8, '
    '"emotional_weight": 0.3, "actionability": 0.9'
    '}, "identity_category": "IDENTITY"}'
)


class TestIngestHappyPath:
    @pytest.mark.asyncio
    async def test_valid_json_adds_entities_and_updates_memory(self) -> None:
        svc = _svc()
        kg = _kg()
        ref = await svc.store(
            content="我是 Claude,能部署服务",
            agent_id="a", session_id="s",
            memory_type=MemoryType.EPISODIC,
            origin=MemoryOrigin.AGENT,
        )
        llm = _MockLLM(response=_VALID_JSON)
        agent = IngestorAgent(svc, llm, kg=kg)

        r = await agent.ingest(
            memory_id=ref.id, content="我是 Claude,能部署服务",
            agent_id="a", session_id="s", origin=MemoryOrigin.AGENT,
        )

        assert r.triggered
        assert not r.degraded
        assert r.entities_added == 2
        assert r.relations_added == 1
        assert r.identity_category == "IDENTITY"
        # weighted importance: 0.25*0.9 + 0.15*0.5 + 0.25*0.8 + 0.15*0.3 + 0.20*0.9
        expected = round(
            0.25 * 0.9 + 0.15 * 0.5 + 0.25 * 0.8 + 0.15 * 0.3 + 0.20 * 0.9, 4
        )
        assert abs(r.importance - expected) < 1e-6

        item = await svc.get(ref.id)
        assert item is not None
        assert abs(item.importance - expected) < 1e-6
        assert item.metadata.get("identity_category") == "IDENTITY"
        assert item.metadata.get("degraded") is False

        # KG actually received the entities.
        assert kg.find_entity_by_name("Claude") is not None
        assert kg.find_entity_by_name("deploy_service") is not None

    @pytest.mark.asyncio
    async def test_llm_called_with_chat_signature(self) -> None:
        svc = _svc()
        kg = _kg()
        ref = await svc.store(
            content="x", agent_id="a", session_id="s",
            origin=MemoryOrigin.AGENT,
        )
        llm = _MockLLM(response=_VALID_JSON)
        agent = IngestorAgent(svc, llm, kg=kg)
        await agent.ingest(ref.id, "x", "a", "s", MemoryOrigin.AGENT)
        assert len(llm.calls) == 1
        # Unified interface: max_tokens + temperature kwargs only.
        assert "max_tokens" in llm.calls[0]["kwargs"]
        assert "temperature" in llm.calls[0]["kwargs"]


class TestDegradation:
    @pytest.mark.asyncio
    async def test_llm_timeout_degrades_to_regex_and_scorer(self) -> None:
        svc = _svc()
        kg = _kg()
        ref = await svc.store(
            content='The "FastAPI" tool is used here',
            agent_id="a", session_id="s",
            origin=MemoryOrigin.AGENT,
        )
        # delay > timeout → asyncio.TimeoutError
        llm = _MockLLM(response=_VALID_JSON, delay=2.0)
        agent = IngestorAgent(svc, llm, kg=kg)

        r = await agent.ingest(
            memory_id=ref.id,
            content='The "FastAPI" tool is used here',
            agent_id="a", session_id="s",
            origin=MemoryOrigin.AGENT,
            timeout=0.1,
        )

        assert r.triggered
        assert r.degraded
        # Regex extractor (kg.extract_and_ingest) was invoked.
        assert r.entities_added >= 1
        item = await svc.get(ref.id)
        assert item is not None
        assert item.metadata.get("degraded") is True
        assert item.metadata.get("identity_category") == "NONE"

    @pytest.mark.asyncio
    async def test_no_llm_client_degrades(self) -> None:
        svc = _svc()
        kg = _kg()
        ref = await svc.store(
            content='Use "PostgreSQL" for storage',
            agent_id="a", session_id="s",
            origin=MemoryOrigin.AGENT,
        )
        agent = IngestorAgent(svc, None, kg=kg)
        r = await agent.ingest(ref.id, 'Use "PostgreSQL" for storage', "a", "s", MemoryOrigin.AGENT)
        assert r.degraded
        assert r.entities_added >= 1


class TestP0RedLine:
    @pytest.mark.asyncio
    async def test_foreground_origin_early_return_never_modifies(self) -> None:
        svc = _svc()
        kg = _kg()
        ref = await svc.store(
            content="user said hello",
            agent_id="a", session_id="s",
            origin=MemoryOrigin.FOREGROUND,
            importance=0.42,
        )
        llm = _MockLLM(response=_VALID_JSON)
        agent = IngestorAgent(svc, llm, kg=kg)

        r = await agent.ingest(
            memory_id=ref.id, content="user said hello",
            agent_id="a", session_id="s",
            origin=MemoryOrigin.FOREGROUND,
        )

        # P0: not triggered, skipped, no LLM call, memory untouched.
        assert r.triggered is False
        assert r.skipped is True
        assert llm.calls == []
        item = await svc.get(ref.id)
        assert item is not None
        assert item.importance == 0.42  # unchanged
        # KG untouched.
        assert kg.find_entity_by_name("Claude") is None

    @pytest.mark.asyncio
    async def test_foreground_origin_string_form_also_skips(self) -> None:
        svc = _svc()
        kg = _kg()
        ref = await svc.store(
            content="fg", agent_id="a", session_id="s",
            origin=MemoryOrigin.FOREGROUND,
        )
        agent = IngestorAgent(svc, _MockLLM(response=_VALID_JSON), kg=kg)
        r = await agent.ingest(ref.id, "fg", "a", "s", "foreground")
        assert r.skipped and not r.triggered


class TestHookBridge:
    @pytest.mark.asyncio
    async def test_ingestor_hook_delegates_to_agent(self) -> None:
        from src.memory.hooks import IngestContext

        svc = _svc()
        kg = _kg()
        ref = await svc.store(
            content="x", agent_id="a", session_id="s",
            origin=MemoryOrigin.AGENT,
        )
        llm = _MockLLM(response=_VALID_JSON)
        agent = IngestorAgent(svc, llm, kg=kg)
        hook = IngestorHook(agent)
        ctx = IngestContext(
            memory_id=ref.id, content="x",
            agent_id="a", session_id="s", origin="agent",
        )
        r = await hook.on_ingest(ctx)
        assert r.triggered
        assert r.entities_added == 2


class TestLayeredTolerance:
    @pytest.mark.asyncio
    async def test_entity_missing_name_skipped_not_fatal(self) -> None:
        svc = _svc()
        kg = _kg()
        ref = await svc.store(
            content="x", agent_id="a", session_id="s",
            origin=MemoryOrigin.AGENT,
        )
        bad = (
            '{"entities": [{"type": "concept"}, '
            '{"name": "Ok", "type": "concept"}], '
            '"importance": {"recency": 0.5}, "identity_category": "KNOWLEDGE"}'
        )
        agent = IngestorAgent(svc, _MockLLM(response=bad), kg=kg)
        r = await agent.ingest(ref.id, "x", "a", "s", MemoryOrigin.AGENT)
        # The valid entity was added; the malformed one skipped (non-fatal).
        assert r.entities_added == 1
        assert r.identity_category == "KNOWLEDGE"
