"""Agent management routes — proxy to orchestrator."""

from typing import Any

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from src.config import ORCHESTRATOR_URL

router = APIRouter()


@router.get("/")
async def list_agents() -> list[dict[str, Any]]:
    """List all agents."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{ORCHESTRATOR_URL}/agents")
        return resp.json()


@router.post("/")
async def create_agent(request: Request) -> dict[str, Any]:
    """Create a new agent."""
    body = await request.json()
    async with httpx.AsyncClient() as client:
        resp = await client.post(f"{ORCHESTRATOR_URL}/agents", json=body)
        return resp.json()


@router.get("/{agent_id}")
async def get_agent(agent_id: str) -> dict[str, Any]:
    """Get agent details."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{ORCHESTRATOR_URL}/agents/{agent_id}")
        return resp.json()


@router.delete("/{agent_id}")
async def delete_agent(agent_id: str) -> dict[str, Any]:
    """Delete an agent."""
    async with httpx.AsyncClient() as client:
        resp = await client.delete(f"{ORCHESTRATOR_URL}/agents/{agent_id}")
        return resp.json()


@router.post("/{agent_id}/run")
async def run_agent(agent_id: str, request: Request) -> StreamingResponse:
    """Trigger agent execution — SSE proxy to orchestrator."""
    body = await request.json()
    body["agent_id"] = agent_id

    async def event_stream():
        async with httpx.AsyncClient() as client:
            async with client.stream(
                "POST",
                f"{ORCHESTRATOR_URL}/execute",
                json=body,
                timeout=60.0,
            ) as resp:
                async for chunk in resp.aiter_text():
                    yield chunk

    return StreamingResponse(event_stream(), media_type="text/event-stream")
