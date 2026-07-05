"""Conversation routes — proxy to orchestrator."""

from typing import Any

from fastapi import APIRouter, Request

from src.config import ORCHESTRATOR_API
from src.config import http_client

router = APIRouter()


@router.get("")
async def list_conversations(request: Request) -> list[dict[str, Any]] | dict[str, Any]:
    """List conversations (proxy to orchestrator /v1/conversations)."""
    params = dict(request.query_params)
    resp = await http_client.get(f"{ORCHESTRATOR_API}/conversations", params=params)
    resp.raise_for_status()
    return resp.json()


@router.get("/{conversation_id}/messages")
async def get_conversation_messages(
    conversation_id: str, request: Request
) -> list[dict[str, Any]] | dict[str, Any]:
    """List messages of a conversation."""
    params = dict(request.query_params)
    resp = await http_client.get(
        f"{ORCHESTRATOR_API}/conversations/{conversation_id}/messages", params=params
    )
    resp.raise_for_status()
    return resp.json()


@router.delete("/{conversation_id}")
async def delete_conversation(conversation_id: str) -> dict[str, Any]:
    """Delete a conversation (cascade messages)."""
    resp = await http_client.delete(f"{ORCHESTRATOR_API}/conversations/{conversation_id}")
    resp.raise_for_status()
    return resp.json()
