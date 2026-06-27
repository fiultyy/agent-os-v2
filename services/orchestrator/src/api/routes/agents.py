"""Agent CRUD routes."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from src.api.models import CreateAgentRequest
from src.services import _state
from src.services.agent_manager import create_agent_data, delete_agent_data

router = APIRouter()


@router.post("/agents")
async def create_agent(req: CreateAgentRequest) -> dict:
    return await create_agent_data(
        name=req.name,
        description=req.description,
        model=req.model,
        system_prompt=req.system_prompt,
        tools=req.tools,
    )


@router.get("/agents")
async def list_agents() -> list[dict]:
    # Prefer the persisted source of truth when PG is wired; any read error
    # degrades silently to the in-memory dict so the endpoint never 500s.
    if _state.pg_store is not None:
        try:
            return await _state.pg_store.list_agents()
        except Exception:
            pass
    return list(_state.agents.values())


@router.get("/agents/{agent_id}")
async def get_agent(agent_id: str) -> dict:
    if _state.pg_store is not None:
        try:
            agent = await _state.pg_store.get_agent(agent_id)
            if agent is not None:
                return agent
        except Exception:
            pass
    agent = _state.agents.get(agent_id)
    if not agent:
        return JSONResponse({"error": "Agent not found"}, status_code=404)
    return agent


@router.delete("/agents/{agent_id}")
async def delete_agent(agent_id: str) -> dict:
    deleted = await delete_agent_data(agent_id)
    if not deleted:
        return JSONResponse({"error": "Agent not found"}, status_code=404)
    return {"deleted": True}
