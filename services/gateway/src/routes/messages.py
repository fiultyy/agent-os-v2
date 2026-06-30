"""Communication routes — proxy to orchestrator."""

from typing import Any

from fastapi import APIRouter, Request

from src.config import ORCHESTRATOR_API
from src.config import http_client

router = APIRouter()


@router.get("")
async def list_messages(request: Request) -> list[dict[str, Any]]:
    """List messages with optional filters."""
    params = dict(request.query_params)
    resp = await http_client.get(f"{ORCHESTRATOR_API}/messages", params=params)
    resp.raise_for_status()
    return resp.json()


@router.post("")
async def send_message(request: Request) -> dict[str, Any]:
    """Send a message between agents."""
    body = await request.json()
    resp = await http_client.post(f"{ORCHESTRATOR_API}/messages", json=body)
    resp.raise_for_status()
    return resp.json()
