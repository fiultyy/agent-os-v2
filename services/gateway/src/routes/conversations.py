"""Conversation observation routes — proxy to conversation-observer."""

from typing import Any

from fastapi import APIRouter, Request

from src.config import CONVERSATION_OBSERVER_URL
from src.main import http_client

router = APIRouter()


@router.post("/")
async def create_conversation(request: Request) -> dict[str, Any]:
    """Create a new conversation."""
    body = await request.json()
    resp = await http_client.post(f"{CONVERSATION_OBSERVER_URL}/conversations", json=body)
    resp.raise_for_status()
    return resp.json()


@router.get("/")
async def list_conversations(agent_id: str = "") -> list[dict[str, Any]]:
    """List all conversations, optionally filtered by agent_id."""
    params = {}
    if agent_id:
        params["agent_id"] = agent_id
    resp = await http_client.get(f"{CONVERSATION_OBSERVER_URL}/conversations", params=params)
    resp.raise_for_status()
    return resp.json()


@router.get("/{conversation_id}")
async def get_conversation(conversation_id: str) -> dict[str, Any]:
    """Get conversation details."""
    resp = await http_client.get(f"{CONVERSATION_OBSERVER_URL}/conversations/{conversation_id}")
    resp.raise_for_status()
    return resp.json()


@router.delete("/{conversation_id}")
async def delete_conversation(conversation_id: str) -> dict[str, Any]:
    """Delete a conversation."""
    resp = await http_client.delete(f"{CONVERSATION_OBSERVER_URL}/conversations/{conversation_id}")
    resp.raise_for_status()
    return resp.json()


@router.post("/{conversation_id}/turns")
async def add_turn(conversation_id: str, request: Request) -> dict[str, Any]:
    """Add a turn to a conversation."""
    body = await request.json()
    resp = await http_client.post(
        f"{CONVERSATION_OBSERVER_URL}/conversations/{conversation_id}/turns", json=body,
    )
    resp.raise_for_status()
    return resp.json()


@router.get("/{conversation_id}/turns")
async def get_turns(conversation_id: str) -> list[dict[str, Any]]:
    """Get all turns in a conversation."""
    resp = await http_client.get(
        f"{CONVERSATION_OBSERVER_URL}/conversations/{conversation_id}/turns",
    )
    resp.raise_for_status()
    return resp.json()


@router.get("/{conversation_id}/context")
async def get_context(conversation_id: str) -> dict[str, Any]:
    """Get conversation context."""
    resp = await http_client.get(
        f"{CONVERSATION_OBSERVER_URL}/conversations/{conversation_id}/context",
    )
    resp.raise_for_status()
    return resp.json()


@router.get("/analytics/stats")
async def get_stats(time_range: str = "7d") -> dict[str, Any]:
    """Get conversation analytics."""
    resp = await http_client.get(
        f"{CONVERSATION_OBSERVER_URL}/analytics/stats", params={"time_range": time_range},
    )
    resp.raise_for_status()
    return resp.json()
