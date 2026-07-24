"""L2: routes.list_ao2_agents — ADR-3 picker source for agent-os-v2 new session.

Pins the fixed contract ``{agents: [{id, name, default}]}`` projected from
``AgentRegistry`` via its public accessors ``default_id()`` / ``iter_agents()``
(ADR-C2 — no private-attr reads in the endpoint). The TUI new-session flow
(worker-C T2) parses this exact shape, so any field rename/rename breaks the
picker — these tests guard the contract end-to-end.
"""

import pytest

from src.harness.routes import list_ao2_agents


def _registry(agents, default_id=None):
    """Build a fake registry exposing AgentRegistry's public accessors.

    ADR-C2: the endpoint now reads ``default_id()`` + ``iter_agents()`` instead
    of the private ``_default_id`` / ``_agents``. This fake mirrors that public
    surface so the test exercises the real call path.
    """
    from src.agent.agent_spec import AgentSpec

    class _FakeRegistry:
        def __init__(self):
            self._agents = {a["id"]: AgentSpec(**a) for a in agents}
            self._default_id = default_id or (
                next((a["id"] for a in agents if a.get("default")), None)
            )

        def default_id(self):
            return self._default_id

        def iter_agents(self):
            return iter(self._agents.items())
    return _FakeRegistry()


@pytest.mark.asyncio
async def test_projects_id_name_default_from_registry(monkeypatch):
    """help agent (default=true) surfaces with name + default=true."""
    from src.services import _state
    reg = _registry([{
        "id": "help", "name": "AO2 新手向导", "default": True,
    }])
    monkeypatch.setattr(_state, "agent_registry", reg)

    res = await list_ao2_agents()
    assert res == {"agents": [{"id": "help", "name": "AO2 新手向导", "default": True}]}


@pytest.mark.asyncio
async def test_name_none_falls_back_to_id(monkeypatch):
    """spec.name is Optional; picker must always show something → id."""
    from src.services import _state
    reg = _registry([{"id": "native", "default": True}])  # no name
    monkeypatch.setattr(_state, "agent_registry", reg)

    res = await list_ao2_agents()
    assert res["agents"][0]["name"] == "native"  # fell back to id
    assert res["agents"][0]["default"] is True


@pytest.mark.asyncio
async def test_default_flag_only_on_default_id(monkeypatch):
    """Multiple agents: only the one matching _default_id has default=true."""
    from src.services import _state
    reg = _registry(
        [{"id": "main"}, {"id": "help", "default": True}, {"id": "explore"}],
        default_id="help",
    )
    monkeypatch.setattr(_state, "agent_registry", reg)

    res = await list_ao2_agents()
    by_id = {a["id"]: a for a in res["agents"]}
    assert by_id["help"]["default"] is True
    assert by_id["main"]["default"] is False
    assert by_id["explore"]["default"] is False


@pytest.mark.asyncio
async def test_registry_none_returns_empty_not_crash(monkeypatch):
    """Engine not booted → registry None. Must return {agents: []}, not 500."""
    from src.services import _state
    monkeypatch.setattr(_state, "agent_registry", None)

    res = await list_ao2_agents()
    assert res == {"agents": []}


@pytest.mark.asyncio
async def test_contract_shape_is_fixed(monkeypatch):
    """ADR-3: shape {agents:[{id,name,default}]} is the parallel contract.
    Field names must NOT drift (TUI T2 parses them with serde)."""
    from src.services import _state
    reg = _registry([{"id": "help", "name": "向导", "default": True}])
    monkeypatch.setattr(_state, "agent_registry", reg)

    res = await list_ao2_agents()
    assert set(res.keys()) == {"agents"}
    agent = res["agents"][0]
    assert set(agent.keys()) == {"id", "name", "default"}
