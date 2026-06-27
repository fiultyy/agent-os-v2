"""L2: bidirectional agent persistence (original L5 folded in).

agent_manager historically had only the *write* side (``store_agent`` in
``create_agent_data``) — on restart ``_state.agents`` was empty and persisted
agents vanished. L2 adds the *read* side: engine.py instantiates PostgresStore,
``initialize()``s it on startup, ``restore_agents_from_pg`` repopulates memory,
and the agents routes read PG when available. PG is faked here so no DB is
needed; the contract exercised is identical to the real PostgresStore surface.
"""

import pytest

from src.services import _state
from src.services.agent_manager import create_agent_data, restore_agents_from_pg
from src.api.routes.agents import list_agents, get_agent


class FakePG:
    """In-memory stand-in for PostgresStore's agent-CRUD surface."""

    def __init__(self) -> None:
        self.agents: dict[str, dict] = {}

    async def store_agent(self, agent: dict) -> None:
        self.agents[agent["id"]] = dict(agent)

    async def get_agent(self, agent_id: str):
        return self.agents.get(agent_id)

    async def list_agents(self):
        return list(self.agents.values())

    async def delete_agent(self, agent_id: str) -> bool:
        return self.agents.pop(agent_id, None) is not None


class FakeMemory:
    async def init_agent_blocks(self, agent_id: str) -> None:
        return None


@pytest.fixture
def clean_state():
    """Isolate _state singletons between tests (agents/pg_store/memory_service)."""
    _state.agents.clear()
    saved_pg = _state.pg_store
    saved_mem = _state.memory_service
    _state.pg_store = None
    _state.memory_service = FakeMemory()
    yield
    _state.agents.clear()
    _state.pg_store = saved_pg
    _state.memory_service = saved_mem


@pytest.mark.asyncio
async def test_create_persists_to_memory_and_pg(clean_state) -> None:
    fake = FakePG()
    _state.pg_store = fake

    agent = await create_agent_data(name="A")

    assert agent["id"] in _state.agents              # memory
    persisted = await fake.get_agent(agent["id"])    # PG
    assert persisted is not None
    assert persisted["name"] == "A"


@pytest.mark.asyncio
async def test_restart_restore_consistency(clean_state) -> None:
    """create → simulate restart (wipe memory) → restore from PG → list matches."""
    fake = FakePG()
    _state.pg_store = fake
    await create_agent_data(name="A1")
    await create_agent_data(name="A2")

    # Simulate restart: in-memory cleared, PG survived.
    _state.agents.clear()
    assert _state.agents == {}

    restored = await restore_agents_from_pg()
    assert restored == 2
    assert {a["name"] for a in _state.agents.values()} == {"A1", "A2"}

    # list_agents reads PG → same set as the restored memory.
    listed = await list_agents()
    assert {a["name"] for a in listed} == {"A1", "A2"}


@pytest.mark.asyncio
async def test_restore_is_noop_without_pg(clean_state) -> None:
    _state.pg_store = None
    assert await restore_agents_from_pg() == 0


@pytest.mark.asyncio
async def test_get_agent_reads_pg_when_available(clean_state) -> None:
    fake = FakePG()
    _state.pg_store = fake
    a = await create_agent_data(name="FromPG")
    _state.agents.clear()              # force the PG read path in get_agent

    got = await get_agent(a["id"])
    assert isinstance(got, dict)
    assert got["name"] == "FromPG"


@pytest.mark.asyncio
async def test_list_agents_falls_back_to_memory_when_pg_none(clean_state) -> None:
    _state.pg_store = None
    _state.agents["x"] = {"id": "x", "name": "MemOnly"}

    listed = await list_agents()
    assert any(a["id"] == "x" for a in listed)


@pytest.mark.asyncio
async def test_restore_does_not_overwrite_existing(clean_state) -> None:
    """restore fills gaps only — an agent already in memory is left untouched."""
    fake = FakePG()
    _state.pg_store = fake
    a = await create_agent_data(name="Persisted")
    _state.agents[a["id"]] = {**a, "name": "InMemoryLive"}  # newer in-memory copy

    restored = await restore_agents_from_pg()
    assert restored == 0  # id already present → not re-filled
    assert _state.agents[a["id"]]["name"] == "InMemoryLive"
