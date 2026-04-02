"""Agent management routes."""

from fastapi import APIRouter

router = APIRouter()


@router.get("/")
async def list_agents():
    """List all agents."""
    # TODO: proxy to orchestrator
    return []


@router.post("/")
async def create_agent():
    """Create a new agent."""
    # TODO: proxy to orchestrator
    return {}


@router.get("/{agent_id}")
async def get_agent(agent_id: str):
    """Get agent details."""
    # TODO: proxy to orchestrator
    return {"id": agent_id}


@router.post("/{agent_id}/run")
async def run_agent(agent_id: str):
    """Trigger agent execution."""
    # TODO: proxy to orchestrator
    return {"agent_id": agent_id, "status": "pending"}
