"""Conversation observation routes."""

from fastapi import APIRouter

router = APIRouter()


@router.get("/")
async def list_conversations():
    """List all conversations."""
    # TODO: proxy to conversation-observer
    return []


@router.get("/{conversation_id}")
async def get_conversation(conversation_id: str):
    """Get conversation details."""
    return {"id": conversation_id}
