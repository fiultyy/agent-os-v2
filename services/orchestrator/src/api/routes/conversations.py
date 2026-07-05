"""Conversation history API routes — 对话历史查询/删除端点。

挂载于 engine /v1 前缀:
- GET /v1/conversations               — list_conversations(agent_id, limit)
- GET /v1/conversations/{id}/messages — list_messages(conversation_id, limit)
- DELETE /v1/conversations/{id}       — delete_conversation(级联删消息)

写入路径是 chat.py /execute 的 record_turn,不经此 API —— 这里纯查询/删除。当
``_state.conversation_registry is None``(装配降级 / 单测未挂载)时,各端点返回
安全空载({"disabled": true} 或空列表),与 pitfall.py 的 None-guard 约定一致。
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from src.services import _state

router = APIRouter()


@router.get("/conversations")
async def list_conversations(
    agent_id: str | None = Query(default=None),
    limit: int = Query(default=50, le=500),
) -> dict | list:
    """List conversations (newest-updated first), with message_count."""
    if _state.conversation_registry is None:
        return {"disabled": True, "items": []}
    return _state.conversation_registry.list_conversations(agent_id=agent_id, limit=limit)


@router.get("/conversations/{conversation_id}/messages")
async def list_conversation_messages(
    conversation_id: str,
    limit: int = Query(default=200, le=1000),
) -> dict | list:
    """List messages of a conversation (oldest first)."""
    if _state.conversation_registry is None:
        return {"disabled": True, "items": []}
    return _state.conversation_registry.list_messages(conversation_id, limit=limit)


@router.delete("/conversations/{conversation_id}")
async def delete_conversation(conversation_id: str) -> dict:
    """Delete a conversation and cascade-delete its messages."""
    if _state.conversation_registry is None:
        return {"disabled": True}
    deleted = _state.conversation_registry.delete_conversation(conversation_id)
    return {"deleted": deleted, "id": conversation_id}
