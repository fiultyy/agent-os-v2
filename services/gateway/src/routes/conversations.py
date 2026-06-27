"""Conversation observation routes — proxy to conversation-observer.

Upstream conversation-observer(8003)已归档(profiles:[aux],见 docker-compose.yml 与
docs/aux-services-archive.md)。归档期间这些端点不再有真实后端:每次 http_client
调用经 _aux_call 兜底,任一上游不可达/报错即返回 502,避免 gateway 因辅助后端缺失
而抛 5xx 噪声。恢复上游后(实线后端或 docker compose --profile aux up)原成功路径
自动复用,无需改代码。
"""

from typing import Any

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from src.config import CONVERSATION_OBSERVER_URL
from src.config import http_client

router = APIRouter()

_AUX_502 = JSONResponse(
    status_code=502,
    content={"detail": "upstream aux service not wired (archived)"},
)


async def _aux_call(coro: Any) -> Any:
    """执行一次到辅助后端的 httpx 调用并兜底 502。

    捕获上游 4xx/5xx(HTTPStatusError)、连接拒绝(ConnectError,归档时的典型态)
    与其余传输层异常(RequestError),统一降级为 502。成功路径返回解析后的 JSON,
    与原 raise_for_status + json() 行为一致。
    """
    try:
        resp = await coro
        resp.raise_for_status()
    except (httpx.HTTPStatusError, httpx.ConnectError, httpx.RequestError):
        return _AUX_502
    return resp.json()


@router.post("/")
async def create_conversation(request: Request) -> dict[str, Any]:
    """Create a new conversation."""
    body = await request.json()
    return await _aux_call(
        http_client.post(f"{CONVERSATION_OBSERVER_URL}/conversations", json=body)
    )


@router.get("/")
async def list_conversations(agent_id: str = "") -> list[dict[str, Any]]:
    """List all conversations, optionally filtered by agent_id."""
    params = {}
    if agent_id:
        params["agent_id"] = agent_id
    return await _aux_call(
        http_client.get(f"{CONVERSATION_OBSERVER_URL}/conversations", params=params)
    )


@router.get("/{conversation_id}")
async def get_conversation(conversation_id: str) -> dict[str, Any]:
    """Get conversation details."""
    return await _aux_call(
        http_client.get(f"{CONVERSATION_OBSERVER_URL}/conversations/{conversation_id}")
    )


@router.delete("/{conversation_id}")
async def delete_conversation(conversation_id: str) -> dict[str, Any]:
    """Delete a conversation."""
    return await _aux_call(
        http_client.delete(f"{CONVERSATION_OBSERVER_URL}/conversations/{conversation_id}")
    )


@router.post("/{conversation_id}/turns")
async def add_turn(conversation_id: str, request: Request) -> dict[str, Any]:
    """Add a turn to a conversation."""
    body = await request.json()
    return await _aux_call(
        http_client.post(
            f"{CONVERSATION_OBSERVER_URL}/conversations/{conversation_id}/turns", json=body,
        )
    )


@router.get("/{conversation_id}/turns")
async def get_turns(conversation_id: str) -> list[dict[str, Any]]:
    """Get all turns in a conversation."""
    return await _aux_call(
        http_client.get(
            f"{CONVERSATION_OBSERVER_URL}/conversations/{conversation_id}/turns",
        )
    )


@router.get("/{conversation_id}/context")
async def get_context(conversation_id: str) -> dict[str, Any]:
    """Get conversation context."""
    return await _aux_call(
        http_client.get(
            f"{CONVERSATION_OBSERVER_URL}/conversations/{conversation_id}/context",
        )
    )


@router.get("/analytics/stats")
async def get_stats(time_range: str = "7d") -> dict[str, Any]:
    """Get conversation analytics."""
    return await _aux_call(
        http_client.get(
            f"{CONVERSATION_OBSERVER_URL}/analytics/stats",
            params={"time_range": time_range},
        )
    )
