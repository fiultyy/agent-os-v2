"""Knowledge Graph and Debug API routes."""

from __future__ import annotations

from fastapi import APIRouter, Query

from src.services import _state

router = APIRouter()


# ── Knowledge Graph API ────────────────────────────────────────────


@router.get("/kg/entities")
async def search_entities(q: str = "", entity_type: str = "", limit: int = Query(default=20, le=500)) -> list[dict]:
    """Search entities in the knowledge graph."""
    if q:
        return _state.knowledge_graph.search_entities(q, entity_type=entity_type or None, limit=limit)
    return []


@router.get("/kg/expand")
async def expand_entity(name: str, depth: int = 2) -> dict:
    """Expand the neighbourhood around an entity."""
    return _state.knowledge_graph.expand(name, depth=depth)


@router.get("/kg/stats")
async def kg_stats() -> dict:
    """Knowledge graph statistics."""
    return _state.knowledge_graph.stats()


# ── Debug API ──────────────────────────────────────────────────────


@router.get("/debug/history")
async def debug_history(
    agent_id: str = "",
    session_id: str = "",
    limit: int = Query(default=50, le=500),
) -> list[dict]:
    """Get execution history for debugging and replay."""
    entries = _state.execution_log
    if agent_id:
        entries = [e for e in entries if e.get("agent_id") == agent_id]
    if session_id:
        entries = [e for e in entries if e.get("session_id") == session_id]
    return list(reversed(entries[-limit:]))


@router.get("/debug/status")
async def debug_status() -> dict:
    """Get overall system debug status."""
    return {
        "agents": len(_state.agents),
        "concurrency": _state.concurrency_controller.get_status(),
        "kg_stats": _state.knowledge_graph.stats(),
    }
