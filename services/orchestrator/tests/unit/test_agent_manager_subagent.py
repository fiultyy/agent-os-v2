"""P0 lifecycle tests for create_subagent / teardown_subagent.

Covers the multi-agent orchestration foundation (see docs/multi-agent-poweron-roadmap.md):
- create_subagent registers an ``is_subagent=True`` agent with parent/type markers
- teardown_subagent removes it AND enforces the is_subagent guard (R5) — persistent
  agents (is_subagent absent/False) are never deleted via this path
- create_subagent does NOT touch memory_service (R1 red-line) — subagents are
  transient, no five-block memory init
- return contract ``{"id": ...}`` locked for conditional_spawner compatibility (R6)
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from src.services import _state
from src.services.agent_manager import create_subagent, teardown_subagent


@pytest.fixture(autouse=True)
def _isolate_state():
    """Snapshot _state singletons so each test runs in a clean agent dict."""
    saved_agents = _state.agents.copy()
    saved_bus = _state.communication_bus
    saved_pg = _state.pg_store
    saved_mem = _state.memory_service
    _state.agents.clear()
    _state.communication_bus = None
    _state.pg_store = None
    _state.memory_service = None
    yield
    _state.agents.clear()
    _state.agents.update(saved_agents)
    _state.communication_bus = saved_bus
    _state.pg_store = saved_pg
    _state.memory_service = saved_mem


# ── create_subagent ────────────────────────────────────────────────────────────


class TestCreateSubagent:
    @pytest.mark.asyncio
    async def test_registers_subagent_with_markers(self):
        result = await create_subagent(
            agent_type="worker",
            config={"name": "w1", "model": "glm-4-flash"},
            parent_id="parent-1",
            session_id="sess-1",
        )
        agent_id = result["id"]
        agent = _state.agents[agent_id]

        assert agent["is_subagent"] is True
        assert agent["parent_id"] == "parent-1"
        assert agent["agent_type"] == "worker"
        assert agent["session_id"] == "sess-1"
        assert agent["status"] == "running"
        assert "spawned_at" in agent

    @pytest.mark.asyncio
    async def test_returns_id_contract(self):
        # R6: spawner L223-228 expects {"id": ...}
        result = await create_subagent(agent_type="t", config={})
        assert isinstance(result, dict)
        assert "id" in result
        assert isinstance(result["id"], str)
        assert result["id"] in _state.agents

    @pytest.mark.asyncio
    async def test_defaults_when_config_empty(self):
        result = await create_subagent(agent_type="reader", config={})
        agent = _state.agents[result["id"]]
        assert agent["name"] == "subagent-reader"
        assert agent["model"] == "glm-4-flash"
        assert agent["tools"] == []
        assert agent["parent_id"] == ""

    @pytest.mark.asyncio
    async def test_does_not_call_memory_service(self):
        # R1 red-line: subagents never init five-block memory.
        mock_mem = MagicMock()
        mock_mem.init_agent_blocks = AsyncMock()
        _state.memory_service = mock_mem

        await create_subagent(agent_type="worker", config={})

        mock_mem.init_agent_blocks.assert_not_called()

    @pytest.mark.asyncio
    async def test_registers_with_communication_bus_when_present(self):
        mock_bus = MagicMock()
        mock_bus.register_agent = MagicMock()
        _state.communication_bus = mock_bus

        result = await create_subagent(
            agent_type="worker", config={}, session_id="sess-x"
        )
        mock_bus.register_agent.assert_called_once_with(result["id"], "sess-x")

    @pytest.mark.asyncio
    async def test_bus_register_failure_does_not_abort(self):
        mock_bus = MagicMock()
        mock_bus.register_agent = MagicMock(side_effect=RuntimeError("boom"))
        _state.communication_bus = mock_bus

        result = await create_subagent(agent_type="worker", config={})
        # agent still registered in-memory despite bus failure
        assert result["id"] in _state.agents

    @pytest.mark.asyncio
    async def test_pg_store_called_when_present(self):
        mock_pg = MagicMock()
        mock_pg.store_agent = AsyncMock()
        _state.pg_store = mock_pg

        await create_subagent(agent_type="worker", config={})
        mock_pg.store_agent.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_pg_store_failure_degrades(self):
        mock_pg = MagicMock()
        mock_pg.store_agent = AsyncMock(side_effect=RuntimeError("pg down"))
        _state.pg_store = mock_pg

        result = await create_subagent(agent_type="worker", config={})
        assert result["id"] in _state.agents


# ── teardown_subagent ──────────────────────────────────────────────────────────


class TestTeardownSubagent:
    @pytest.mark.asyncio
    async def test_removes_subagent(self):
        result = await create_subagent(
            agent_type="worker", config={}, session_id="sess-1"
        )
        agent_id = result["id"]
        assert agent_id in _state.agents

        ok = await teardown_subagent(agent_id)
        assert ok is True
        assert agent_id not in _state.agents

    @pytest.mark.asyncio
    async def test_guard_refuses_persistent_agent_default(self):
        # R5: is_subagent absent → must refuse, leave agent intact.
        _state.agents["persist-1"] = {
            "id": "persist-1",
            "name": "long-lived",
            "status": "idle",
            # NOTE: no is_subagent field
        }
        ok = await teardown_subagent("persist-1")
        assert ok is False
        assert "persist-1" in _state.agents

    @pytest.mark.asyncio
    async def test_guard_refuses_explicit_false(self):
        # R5: is_subagent explicitly False → must also refuse.
        _state.agents["persist-2"] = {
            "id": "persist-2",
            "name": "long-lived",
            "is_subagent": False,
        }
        ok = await teardown_subagent("persist-2")
        assert ok is False
        assert "persist-2" in _state.agents

    @pytest.mark.asyncio
    async def test_missing_agent_returns_false(self):
        ok = await teardown_subagent("does-not-exist")
        assert ok is False

    @pytest.mark.asyncio
    async def test_unregisters_from_bus(self):
        mock_bus = MagicMock()
        mock_bus.unregister_agent = MagicMock()
        _state.communication_bus = mock_bus

        result = await create_subagent(
            agent_type="worker", config={}, session_id="sess-9"
        )
        await teardown_subagent(result["id"])
        mock_bus.unregister_agent.assert_called_once_with(result["id"], "sess-9")

    @pytest.mark.asyncio
    async def test_pg_delete_called_when_present(self):
        mock_pg = MagicMock()
        mock_pg.store_agent = AsyncMock()
        mock_pg.delete_agent = AsyncMock()
        _state.pg_store = mock_pg

        result = await create_subagent(agent_type="worker", config={})
        await teardown_subagent(result["id"])
        mock_pg.delete_agent.assert_awaited_once()
