"""Prompt management routes — proxy to prompt-manager."""

from typing import Any

import httpx
from fastapi import APIRouter, Request

from src.config import PROMPT_MANAGER_URL

router = APIRouter()


@router.post("/")
async def create_prompt(request: Request) -> dict[str, Any]:
    """Create a prompt template."""
    body = await request.json()
    async with httpx.AsyncClient() as client:
        resp = await client.post(f"{PROMPT_MANAGER_URL}/templates", json=body)
        return resp.json()


@router.get("/")
async def list_prompts() -> list[dict[str, Any]]:
    """List all prompt templates."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{PROMPT_MANAGER_URL}/templates")
        return resp.json()


@router.get("/{prompt_id}")
async def get_prompt(prompt_id: str) -> dict[str, Any]:
    """Get prompt template details."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{PROMPT_MANAGER_URL}/templates/{prompt_id}")
        return resp.json()


@router.put("/{prompt_id}")
async def update_prompt(prompt_id: str, request: Request) -> dict[str, Any]:
    """Update a prompt template."""
    body = await request.json()
    async with httpx.AsyncClient() as client:
        resp = await client.put(f"{PROMPT_MANAGER_URL}/templates/{prompt_id}", json=body)
        return resp.json()


@router.delete("/{prompt_id}")
async def delete_prompt(prompt_id: str) -> dict[str, Any]:
    """Delete a prompt template."""
    async with httpx.AsyncClient() as client:
        resp = await client.delete(f"{PROMPT_MANAGER_URL}/templates/{prompt_id}")
        return resp.json()


@router.post("/{prompt_id}/render")
async def render_prompt(prompt_id: str, request: Request) -> dict[str, Any]:
    """Render a prompt template with variables."""
    body = await request.json()
    async with httpx.AsyncClient() as client:
        resp = await client.post(f"{PROMPT_MANAGER_URL}/templates/{prompt_id}/render", json=body)
        return resp.json()


@router.get("/{prompt_id}/versions")
async def list_versions(prompt_id: str) -> dict[str, Any]:
    """List all versions of a prompt template."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{PROMPT_MANAGER_URL}/templates/{prompt_id}/versions")
        return resp.json()
