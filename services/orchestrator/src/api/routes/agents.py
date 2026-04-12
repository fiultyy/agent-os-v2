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
    return list(_state.agents.values())


@router.get("/agents/{agent_id}")
async def get_agent(agent_id: str) -> dict:
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
