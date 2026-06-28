"""Agent lifecycle management — CRUD helpers and default agent creation."""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from typing import Any

from src.services import _state


async def create_agent_data(
    name: str = "New Agent",
    description: str = "",
    model: str = "glm-4-flash",
    system_prompt: str = "",
    tools: list[str] | None = None,
) -> dict[str, Any]:
    """Create a new agent and persist it. Returns the agent dict."""
    if tools is None:
        tools = []
    agent_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    agent = {
        "id": agent_id,
        "name": name,
        "description": description,
        "status": "idle",
        "model": model,
        "system_prompt": system_prompt,
        "tools": tools,
        "created_at": now,
        "updated_at": now,
    }
    _state.agents[agent_id] = agent
    if _state.pg_store is not None:
        await _state.pg_store.store_agent(agent)
    await _state.memory_service.init_agent_blocks(agent_id)
    return agent


async def delete_agent_data(agent_id: str) -> bool:
    """Delete an agent. Returns True if found and deleted."""
    if agent_id not in _state.agents:
        return False
    del _state.agents[agent_id]
    if _state.pg_store is not None:
        await _state.pg_store.delete_agent(agent_id)
    return True


async def init_default_agent() -> dict[str, Any] | None:
    """Auto-create a default agent if none exist."""
    if _state.agents:
        return None
    agent = await create_agent_data(
        name="默认助手",
        description="Agent OS 默认智能助手，开箱即用",
        model=os.environ.get("LLM_MODEL", "glm-4-flash"),
        system_prompt="你是 Agent OS 的默认助手。你善于用中文回答各类问题，提供有帮助的建议。回答要简洁明了。",
    )
    print(f"Default agent initialized: {agent['id']}")
    return agent


async def create_subagent(
    agent_type: str,
    config: dict[str, Any],
    parent_id: str = "",
    session_id: str = "",
) -> dict[str, Any]:
    """Create a *transient* subagent for multi-agent orchestration.

    Unlike :func:`create_agent_data`, a subagent is a short-lived runtime entity
    (spawned by ConditionalSpawner / MetaAgentNode) — it is **never** persisted
    to the five-block memory layout. Memory red-line R1: this function MUST NOT
    call ``memory_service.init_agent_blocks`` or any ``memory_*`` helper;
    subagents carry no long-term memory.

    Contract (R6, locked by ``conditional_spawner._create_subagent`` L223-228):
    always returns ``{"id": <uuid>}``.

    Basic fields (name/model/system_prompt) are taken from ``config`` when
    present, otherwise defaulted. The agent dict additionally carries:
    ``is_subagent=True, parent_id, agent_type, spawned_at, status="running"``.

    Side effects are best-effort and None-safe:
    - ``communication_bus.register_agent(id, session_id)`` when the bus exists;
    - ``pg_store.store_agent(agent)`` when PG is wired (failure degrades — the
      in-memory dict is the source of truth for subagents).
    """
    agent_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    agent: dict[str, Any] = {
        "id": agent_id,
        "name": config.get("name", f"subagent-{agent_type}"),
        "description": config.get("description", ""),
        "status": "running",
        "model": config.get("model", "glm-4-flash"),
        "system_prompt": config.get("system_prompt", ""),
        "tools": list(config.get("tools", [])),
        # ── subagent-specific markers ──
        "is_subagent": True,
        "parent_id": parent_id,
        "agent_type": agent_type,
        "session_id": session_id,
        "spawned_at": now,
        "created_at": now,
        "updated_at": now,
    }
    _state.agents[agent_id] = agent

    # communication_bus: None-safe register (broadcast delivery membership)
    bus = _state.communication_bus
    if bus is not None:
        try:
            bus.register_agent(agent_id, session_id)
        except Exception:  # bus failure must not abort subagent creation
            pass

    # pg_store: best-effort persist, degrade to in-memory only
    if _state.pg_store is not None:
        try:
            await _state.pg_store.store_agent(agent)
        except Exception:
            pass

    # NOTE(intentional): no memory_service.init_agent_blocks call — R1 red-line.
    return {"id": agent_id}


async def teardown_subagent(agent_id: str) -> bool:
    """Tear down a previously-spawned subagent.

    Red-line R5: the ``is_subagent is True`` guard is mandatory — a persistent
    agent (``is_subagent`` absent or False) is NEVER removed here, preventing
    accidental deletion of long-lived agents via the subagent teardown path.

    Returns ``True`` on successful removal, ``False`` if the agent is missing or
    is not a subagent. Side effects (``communication_bus.unregister_agent``,
    ``pg_store.delete_agent``) are None-safe / best-effort.
    """
    agent = _state.agents.get(agent_id)
    if agent is None:
        return False
    if agent.get("is_subagent") is not True:
        # R5 guard: refuse to tear down non-subagent (persistent) agents.
        return False

    session_id = agent.get("session_id", "")
    bus = _state.communication_bus
    if bus is not None:
        try:
            bus.unregister_agent(agent_id, session_id)
        except Exception:
            pass

    del _state.agents[agent_id]

    if _state.pg_store is not None:
        try:
            await _state.pg_store.delete_agent(agent_id)
        except Exception:
            pass

    return True


async def restore_agents_from_pg() -> int:
    """Restore persisted agents from PG into the in-memory ``agents`` dict.

    ``agent_manager`` historically only had the *write* side (``store_agent``
    in :func:`create_agent_data`); on restart ``_state.agents`` started empty
    and every previously-persisted agent was lost. engine.py calls this on the
    startup hook right after ``pg_store.initialize()`` so the read side closes
    the loop. Agents already present in memory (e.g. created earlier in the
    same boot) are left untouched — PG only *fills gaps*, never overwrites.
    """
    if _state.pg_store is None:
        return 0
    try:
        agents = await _state.pg_store.list_agents()
    except Exception:
        # PG read failure is non-fatal — fall back to whatever is in memory.
        return 0

    count = 0
    for agent in agents:
        aid = agent.get("id") if isinstance(agent, dict) else None
        if aid and aid not in _state.agents:
            _state.agents[aid] = agent
            count += 1
    return count
