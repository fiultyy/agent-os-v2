"""Knowledge graph routes — proxy to orchestrator."""

from typing import Any

from fastapi import APIRouter, Request

from src.config import ORCHESTRATOR_URL
from src.config import http_client

router = APIRouter()


@router.get("/entities")
async def search_entities(request: Request) -> list[dict[str, Any]]:
    """Search knowledge graph entities."""
    params = dict(request.query_params)
    resp = await http_client.get(f"{ORCHESTRATOR_URL}/kg/entities", params=params)
    resp.raise_for_status()
    return resp.json()


@router.get("/expand")
async def expand_entity(request: Request) -> dict[str, Any]:
    """Expand entity relationships."""
    params = dict(request.query_params)
    resp = await http_client.get(f"{ORCHESTRATOR_URL}/kg/expand", params=params)
    resp.raise_for_status()
    return resp.json()


@router.get("/stats")
async def get_kg_stats() -> dict[str, Any]:
    """Get knowledge graph statistics."""
    resp = await http_client.get(f"{ORCHESTRATOR_URL}/kg/stats")
    resp.raise_for_status()
    return resp.json()
