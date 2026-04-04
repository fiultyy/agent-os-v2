"""Resource management routes — proxy to resource-manager."""

from typing import Any

import httpx
from fastapi import APIRouter, Request

from src.config import RESOURCE_MANAGER_URL

router = APIRouter()


@router.post("/providers")
async def register_provider(request: Request) -> dict[str, Any]:
    """Register a new LLM provider."""
    body = await request.json()
    async with httpx.AsyncClient() as client:
        resp = await client.post(f"{RESOURCE_MANAGER_URL}/providers", json=body)
        return resp.json()


@router.get("/providers")
async def list_providers() -> list[dict[str, Any]]:
    """List configured providers."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{RESOURCE_MANAGER_URL}/providers")
        return resp.json()


@router.get("/providers/{provider_id}")
async def get_provider(provider_id: str) -> dict[str, Any]:
    """Get provider details."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{RESOURCE_MANAGER_URL}/providers/{provider_id}")
        return resp.json()


@router.delete("/providers/{provider_id}")
async def delete_provider(provider_id: str) -> dict[str, Any]:
    """Delete a provider."""
    async with httpx.AsyncClient() as client:
        resp = await client.delete(f"{RESOURCE_MANAGER_URL}/providers/{provider_id}")
        return resp.json()


@router.post("/models")
async def register_model(request: Request) -> dict[str, Any]:
    """Register a model."""
    body = await request.json()
    async with httpx.AsyncClient() as client:
        resp = await client.post(f"{RESOURCE_MANAGER_URL}/models", json=body)
        return resp.json()


@router.get("/models")
async def list_models(provider: str = "") -> list[dict[str, Any]]:
    """List available models, optionally filtered by provider."""
    params = {}
    if provider:
        params["provider"] = provider
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{RESOURCE_MANAGER_URL}/models", params=params)
        return resp.json()


@router.get("/models/{model_alias}")
async def get_model(model_alias: str) -> dict[str, Any]:
    """Get model details."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{RESOURCE_MANAGER_URL}/models/{model_alias}")
        return resp.json()


@router.delete("/models/{model_alias}")
async def delete_model(model_alias: str) -> dict[str, Any]:
    """Delete a model."""
    async with httpx.AsyncClient() as client:
        resp = await client.delete(f"{RESOURCE_MANAGER_URL}/models/{model_alias}")
        return resp.json()


@router.post("/route/resolve")
async def resolve_model(request: Request) -> dict[str, Any]:
    """Resolve a model alias to provider + model_id."""
    body = await request.json()
    async with httpx.AsyncClient() as client:
        resp = await client.post(f"{RESOURCE_MANAGER_URL}/route/resolve", json=body)
        return resp.json()


@router.post("/route/cheapest")
async def cheapest_model(request: Request) -> dict[str, Any]:
    """Find the cheapest available model."""
    body = await request.json()
    async with httpx.AsyncClient() as client:
        resp = await client.post(f"{RESOURCE_MANAGER_URL}/route/cheapest", json=body)
        return resp.json()
