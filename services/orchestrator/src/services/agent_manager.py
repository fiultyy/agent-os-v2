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
