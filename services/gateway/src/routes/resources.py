"""Resource management routes."""

from fastapi import APIRouter

router = APIRouter()


@router.get("/providers")
async def list_providers():
    """List configured providers."""
    # TODO: proxy to resource-manager
    return []


@router.get("/models")
async def list_models():
    """List available models."""
    # TODO: proxy to resource-manager
    return []
