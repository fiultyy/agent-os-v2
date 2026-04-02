"""Prompt management routes."""

from fastapi import APIRouter

router = APIRouter()


@router.get("/")
async def list_prompts():
    """List all prompt templates."""
    # TODO: proxy to prompt-manager
    return []


@router.post("/")
async def create_prompt():
    """Create a prompt template."""
    return {}


@router.get("/{prompt_id}")
async def get_prompt(prompt_id: str):
    """Get prompt template details."""
    return {"id": prompt_id}
