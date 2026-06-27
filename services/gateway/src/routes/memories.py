"""Memory routes — proxy to orchestrator."""

from typing import Any

from fastapi import APIRouter, Request

from src.config import ORCHESTRATOR_API
from src.config import http_client

router = APIRouter()


@router.get("/")
async def list_memories(request: Request) -> list[dict[str, Any]] | dict[str, Any]:
    """List memories with optional filters."""
    params = dict(request.query_params)
    resp = await http_client.get(f"{ORCHESTRATOR_API}/memories", params=params)
    resp.raise_for_status()
    return resp.json()


@router.post("/")
async def store_memory(request: Request) -> dict[str, Any]:
    """Store a new memory."""
    body = await request.json()
    resp = await http_client.post(f"{ORCHESTRATOR_API}/memories", json=body)
    resp.raise_for_status()
    return resp.json()


@router.get("/layers")
async def get_memory_layers(request: Request) -> dict[str, Any]:
    """Get memory layer counts for an agent."""
    params = dict(request.query_params)
    resp = await http_client.get(f"{ORCHESTRATOR_API}/memories/layers", params=params)
    resp.raise_for_status()
    return resp.json()


@router.delete("/{memory_id}")
async def delete_memory(memory_id: str) -> dict[str, Any]:
    """Delete a memory."""
    resp = await http_client.delete(f"{ORCHESTRATOR_API}/memories/{memory_id}")
    resp.raise_for_status()
    return resp.json()
