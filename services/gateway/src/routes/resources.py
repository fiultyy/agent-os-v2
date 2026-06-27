"""Resource management routes — proxy to resource-manager.

Upstream resource-manager(8004)已归档(profiles:[aux],见 docker-compose.yml 与
docs/aux-services-archive.md)。归档期间这些端点不再有真实后端:每次 http_client
调用经 _aux_call 兜底,任一上游不可达/报错即返回 502,避免 gateway 因辅助后端缺失
而抛 5xx 噪声。恢复上游后(实线后端或 docker compose --profile aux up)原成功路径
自动复用,无需改代码。
"""

from typing import Any

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from src.config import RESOURCE_MANAGER_URL
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


@router.post("/providers")
async def register_provider(request: Request) -> dict[str, Any]:
    """Register a new LLM provider."""
    body = await request.json()
    return await _aux_call(
        http_client.post(f"{RESOURCE_MANAGER_URL}/providers", json=body)
    )


@router.get("/providers")
async def list_providers() -> list[dict[str, Any]]:
    """List configured providers."""
    return await _aux_call(http_client.get(f"{RESOURCE_MANAGER_URL}/providers"))


@router.get("/providers/{provider_id}")
async def get_provider(provider_id: str) -> dict[str, Any]:
    """Get provider details."""
    return await _aux_call(
        http_client.get(f"{RESOURCE_MANAGER_URL}/providers/{provider_id}")
    )


@router.delete("/providers/{provider_id}")
async def delete_provider(provider_id: str) -> dict[str, Any]:
    """Delete a provider."""
    return await _aux_call(
        http_client.delete(f"{RESOURCE_MANAGER_URL}/providers/{provider_id}")
    )


@router.post("/models")
async def register_model(request: Request) -> dict[str, Any]:
    """Register a model."""
    body = await request.json()
    return await _aux_call(http_client.post(f"{RESOURCE_MANAGER_URL}/models", json=body))


@router.get("/models")
async def list_models(provider: str = "") -> list[dict[str, Any]]:
    """List available models, optionally filtered by provider."""
    params = {}
    if provider:
        params["provider"] = provider
    return await _aux_call(
        http_client.get(f"{RESOURCE_MANAGER_URL}/models", params=params)
    )


@router.get("/models/{model_alias}")
async def get_model(model_alias: str) -> dict[str, Any]:
    """Get model details."""
    return await _aux_call(
        http_client.get(f"{RESOURCE_MANAGER_URL}/models/{model_alias}")
    )


@router.delete("/models/{model_alias}")
async def delete_model(model_alias: str) -> dict[str, Any]:
    """Delete a model."""
    return await _aux_call(
        http_client.delete(f"{RESOURCE_MANAGER_URL}/models/{model_alias}")
    )


@router.post("/route/resolve")
async def resolve_model(request: Request) -> dict[str, Any]:
    """Resolve a model alias to provider + model_id."""
    body = await request.json()
    return await _aux_call(
        http_client.post(f"{RESOURCE_MANAGER_URL}/route/resolve", json=body)
    )


@router.post("/route/cheapest")
async def cheapest_model(request: Request) -> dict[str, Any]:
    """Find the cheapest available model."""
    body = await request.json()
    return await _aux_call(
        http_client.post(f"{RESOURCE_MANAGER_URL}/route/cheapest", json=body)
    )
