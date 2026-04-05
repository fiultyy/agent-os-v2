"""Agent management routes — proxy to orchestrator."""

from typing import Any

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from src.config import ORCHESTRATOR_URL
from src.config import http_client

router = APIRouter()


@router.get("/")
async def list_agents() -> list[dict[str, Any]]:
    """List all agents."""
    resp = await http_client.get(f"{ORCHESTRATOR_URL}/agents")
    resp.raise_for_status()
    return resp.json()


@router.post("/")
async def create_agent(request: Request) -> dict[str, Any]:
    """Create a new agent."""
    body = await request.json()
    resp = await http_client.post(f"{ORCHESTRATOR_URL}/agents", json=body)
    resp.raise_for_status()
    return resp.json()


@router.get("/{agent_id}")
async def get_agent(agent_id: str) -> dict[str, Any]:
    """Get agent details."""
    resp = await http_client.get(f"{ORCHESTRATOR_URL}/agents/{agent_id}")
    resp.raise_for_status()
    return resp.json()


@router.delete("/{agent_id}")
async def delete_agent(agent_id: str) -> dict[str, Any]:
    """Delete an agent."""
    resp = await http_client.delete(f"{ORCHESTRATOR_URL}/agents/{agent_id}")
    resp.raise_for_status()
    return resp.json()


@router.post("/{agent_id}/run")
async def run_agent(agent_id: str, request: Request) -> StreamingResponse:
    """Trigger agent execution — SSE proxy to orchestrator."""
    body = await request.json()
    body["agent_id"] = agent_id

    async def event_stream():
        async with http_client.stream(
            "POST",
            f"{ORCHESTRATOR_URL}/execute",
            json=body,
            timeout=60.0,
        ) as resp:
            resp.raise_for_status()
            async for chunk in resp.aiter_text():
                yield chunk

    return StreamingResponse(event_stream(), media_type="text/event-stream")
