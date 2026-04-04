"""Conversation observation routes — proxy to conversation-observer."""

from typing import Any

import httpx
from fastapi import APIRouter, Request

from src.config import CONVERSATION_OBSERVER_URL

router = APIRouter()


@router.post("/")
async def create_conversation(request: Request) -> dict[str, Any]:
    """Create a new conversation."""
    body = await request.json()
    async with httpx.AsyncClient() as client:
        resp = await client.post(f"{CONVERSATION_OBSERVER_URL}/conversations", json=body)
        return resp.json()


@router.get("/")
async def list_conversations(agent_id: str = "") -> list[dict[str, Any]]:
    """List all conversations, optionally filtered by agent_id."""
    params = {}
    if agent_id:
        params["agent_id"] = agent_id
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{CONVERSATION_OBSERVER_URL}/conversations", params=params)
        return resp.json()


@router.get("/{conversation_id}")
async def get_conversation(conversation_id: str) -> dict[str, Any]:
    """Get conversation details."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{CONVERSATION_OBSERVER_URL}/conversations/{conversation_id}")
        return resp.json()


@router.delete("/{conversation_id}")
async def delete_conversation(conversation_id: str) -> dict[str, Any]:
    """Delete a conversation."""
    async with httpx.AsyncClient() as client:
        resp = await client.delete(f"{CONVERSATION_OBSERVER_URL}/conversations/{conversation_id}")
        return resp.json()


@router.post("/{conversation_id}/turns")
async def add_turn(conversation_id: str, request: Request) -> dict[str, Any]:
    """Add a turn to a conversation."""
    body = await request.json()
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{CONVERSATION_OBSERVER_URL}/conversations/{conversation_id}/turns", json=body,
        )
        return resp.json()


@router.get("/{conversation_id}/turns")
async def get_turns(conversation_id: str) -> list[dict[str, Any]]:
    """Get all turns in a conversation."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{CONVERSATION_OBSERVER_URL}/conversations/{conversation_id}/turns",
        )
        return resp.json()


@router.get("/{conversation_id}/context")
async def get_context(conversation_id: str) -> dict[str, Any]:
    """Get conversation context."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{CONVERSATION_OBSERVER_URL}/conversations/{conversation_id}/context",
        )
        return resp.json()


@router.get("/analytics/stats")
async def get_stats(time_range: str = "7d") -> dict[str, Any]:
    """Get conversation analytics."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{CONVERSATION_OBSERVER_URL}/analytics/stats", params={"time_range": time_range},
        )
        return resp.json()
