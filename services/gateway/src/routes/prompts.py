"""Prompt management routes — proxy to prompt-manager."""

from typing import Any

from fastapi import APIRouter, Request

from src.config import PROMPT_MANAGER_URL
from src.config import http_client

router = APIRouter()


@router.post("/")
async def create_prompt(request: Request) -> dict[str, Any]:
    """Create a prompt template."""
    body = await request.json()
    resp = await http_client.post(f"{PROMPT_MANAGER_URL}/templates", json=body)
    resp.raise_for_status()
    return resp.json()


@router.get("/")
async def list_prompts() -> list[dict[str, Any]]:
    """List all prompt templates."""
    resp = await http_client.get(f"{PROMPT_MANAGER_URL}/templates")
    resp.raise_for_status()
    return resp.json()


@router.get("/{prompt_id}")
async def get_prompt(prompt_id: str) -> dict[str, Any]:
    """Get prompt template details."""
    resp = await http_client.get(f"{PROMPT_MANAGER_URL}/templates/{prompt_id}")
    resp.raise_for_status()
    return resp.json()


@router.put("/{prompt_id}")
async def update_prompt(prompt_id: str, request: Request) -> dict[str, Any]:
    """Update a prompt template."""
    body = await request.json()
    resp = await http_client.put(f"{PROMPT_MANAGER_URL}/templates/{prompt_id}", json=body)
    resp.raise_for_status()
    return resp.json()


@router.delete("/{prompt_id}")
async def delete_prompt(prompt_id: str) -> dict[str, Any]:
    """Delete a prompt template."""
    resp = await http_client.delete(f"{PROMPT_MANAGER_URL}/templates/{prompt_id}")
    resp.raise_for_status()
    return resp.json()


@router.post("/{prompt_id}/render")
async def render_prompt(prompt_id: str, request: Request) -> dict[str, Any]:
    """Render a prompt template with variables."""
    body = await request.json()
    resp = await http_client.post(f"{PROMPT_MANAGER_URL}/templates/{prompt_id}/render", json=body)
    resp.raise_for_status()
    return resp.json()


@router.get("/{prompt_id}/versions")
async def list_versions(prompt_id: str) -> dict[str, Any]:
    """List all versions of a prompt template."""
    resp = await http_client.get(f"{PROMPT_MANAGER_URL}/templates/{prompt_id}/versions")
    resp.raise_for_status()
    return resp.json()
