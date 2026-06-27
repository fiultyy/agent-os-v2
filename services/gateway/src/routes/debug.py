"""Debug / execution history routes — proxy to orchestrator."""

from typing import Any

from fastapi import APIRouter, Request

from src.config import ORCHESTRATOR_API
from src.config import http_client

router = APIRouter()


@router.get("/history")
async def get_execution_history(request: Request) -> list[dict[str, Any]]:
    """Get execution history with optional filters."""
    params = dict(request.query_params)
    resp = await http_client.get(f"{ORCHESTRATOR_API}/debug/history", params=params)
    resp.raise_for_status()
    return resp.json()


@router.get("/status")
async def get_debug_status(request: Request) -> dict[str, Any]:
    """Get debug status."""
    params = dict(request.query_params)
    resp = await http_client.get(f"{ORCHESTRATOR_API}/debug/status", params=params)
    resp.raise_for_status()
    return resp.json()
