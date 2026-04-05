"""Resource management routes — proxy to resource-manager."""

from typing import Any

from fastapi import APIRouter, Request

from src.config import RESOURCE_MANAGER_URL
from src.main import http_client

router = APIRouter()


@router.post("/providers")
async def register_provider(request: Request) -> dict[str, Any]:
    """Register a new LLM provider."""
    body = await request.json()
    resp = await http_client.post(f"{RESOURCE_MANAGER_URL}/providers", json=body)
    resp.raise_for_status()
    return resp.json()


@router.get("/providers")
async def list_providers() -> list[dict[str, Any]]:
    """List configured providers."""
    resp = await http_client.get(f"{RESOURCE_MANAGER_URL}/providers")
    resp.raise_for_status()
    return resp.json()


@router.get("/providers/{provider_id}")
async def get_provider(provider_id: str) -> dict[str, Any]:
    """Get provider details."""
    resp = await http_client.get(f"{RESOURCE_MANAGER_URL}/providers/{provider_id}")
    resp.raise_for_status()
    return resp.json()


@router.delete("/providers/{provider_id}")
async def delete_provider(provider_id: str) -> dict[str, Any]:
    """Delete a provider."""
    resp = await http_client.delete(f"{RESOURCE_MANAGER_URL}/providers/{provider_id}")
    resp.raise_for_status()
    return resp.json()


@router.post("/models")
async def register_model(request: Request) -> dict[str, Any]:
    """Register a model."""
    body = await request.json()
    resp = await http_client.post(f"{RESOURCE_MANAGER_URL}/models", json=body)
    resp.raise_for_status()
    return resp.json()


@router.get("/models")
async def list_models(provider: str = "") -> list[dict[str, Any]]:
    """List available models, optionally filtered by provider."""
    params = {}
    if provider:
        params["provider"] = provider
    resp = await http_client.get(f"{RESOURCE_MANAGER_URL}/models", params=params)
    resp.raise_for_status()
    return resp.json()


@router.get("/models/{model_alias}")
async def get_model(model_alias: str) -> dict[str, Any]:
    """Get model details."""
    resp = await http_client.get(f"{RESOURCE_MANAGER_URL}/models/{model_alias}")
    resp.raise_for_status()
    return resp.json()


@router.delete("/models/{model_alias}")
async def delete_model(model_alias: str) -> dict[str, Any]:
    """Delete a model."""
    resp = await http_client.delete(f"{RESOURCE_MANAGER_URL}/models/{model_alias}")
    resp.raise_for_status()
    return resp.json()


@router.post("/route/resolve")
async def resolve_model(request: Request) -> dict[str, Any]:
    """Resolve a model alias to provider + model_id."""
    body = await request.json()
    resp = await http_client.post(f"{RESOURCE_MANAGER_URL}/route/resolve", json=body)
    resp.raise_for_status()
    return resp.json()


@router.post("/route/cheapest")
async def cheapest_model(request: Request) -> dict[str, Any]:
    """Find the cheapest available model."""
    body = await request.json()
    resp = await http_client.post(f"{RESOURCE_MANAGER_URL}/route/cheapest", json=body)
    resp.raise_for_status()
    return resp.json()
