"""Chat route — proxy to orchestrator /chat."""

from typing import Any

from fastapi import APIRouter, Request

from src.config import ORCHESTRATOR_URL
from src.config import http_client

router = APIRouter()


@router.post("/")
async def chat(request: Request) -> dict[str, Any]:
    """Proxy chat request to orchestrator."""
    body = await request.json()
    resp = await http_client.post(f"{ORCHESTRATOR_URL}/chat", json=body, timeout=60.0)
    resp.raise_for_status()
    return resp.json()


@router.post("")
async def chat_no_slash(request: Request) -> dict[str, Any]:
    """Proxy chat request — no trailing slash variant."""
    return await chat(request)
