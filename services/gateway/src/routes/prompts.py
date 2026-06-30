"""Prompt management routes — proxy to prompt-manager.

Upstream prompt-manager(8002)已归档(profiles:[aux],见 docker-compose.yml 与
docs/aux-services-archive.md)。归档期间这些端点不再有真实后端:每次 http_client
调用经 _aux_call 兜底,任一上游不可达/报错即返回 502,避免 gateway 因辅助后端缺失
而抛 5xx 噪声。恢复上游后(实线后端或 docker compose --profile aux up)原成功路径
自动复用,无需改代码。
"""

from typing import Any

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from src.config import PROMPT_MANAGER_URL
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


@router.post("")
async def create_prompt(request: Request) -> dict[str, Any]:
    """Create a prompt template."""
    body = await request.json()
    return await _aux_call(http_client.post(f"{PROMPT_MANAGER_URL}/templates", json=body))


@router.get("")
async def list_prompts() -> list[dict[str, Any]]:
    """List all prompt templates."""
    return await _aux_call(http_client.get(f"{PROMPT_MANAGER_URL}/templates"))


@router.get("/{prompt_id}")
async def get_prompt(prompt_id: str) -> dict[str, Any]:
    """Get prompt template details."""
    return await _aux_call(http_client.get(f"{PROMPT_MANAGER_URL}/templates/{prompt_id}"))


@router.put("/{prompt_id}")
async def update_prompt(prompt_id: str, request: Request) -> dict[str, Any]:
    """Update a prompt template."""
    body = await request.json()
    return await _aux_call(
        http_client.put(f"{PROMPT_MANAGER_URL}/templates/{prompt_id}", json=body)
    )


@router.delete("/{prompt_id}")
async def delete_prompt(prompt_id: str) -> dict[str, Any]:
    """Delete a prompt template."""
    return await _aux_call(
        http_client.delete(f"{PROMPT_MANAGER_URL}/templates/{prompt_id}")
    )


@router.post("/{prompt_id}/render")
async def render_prompt(prompt_id: str, request: Request) -> dict[str, Any]:
    """Render a prompt template with variables."""
    body = await request.json()
    return await _aux_call(
        http_client.post(f"{PROMPT_MANAGER_URL}/templates/{prompt_id}/render", json=body)
    )


@router.get("/{prompt_id}/versions")
async def list_versions(prompt_id: str) -> dict[str, Any]:
    """List all versions of a prompt template."""
    return await _aux_call(
        http_client.get(f"{PROMPT_MANAGER_URL}/templates/{prompt_id}/versions")
    )
