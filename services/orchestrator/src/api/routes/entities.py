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
    main_c = _state.llm_client
    side_c = _state.side_llm_client
    main_model = (getattr(main_c, "anthropic_model", None)
                  or getattr(main_c, "default_model", None))
    return {
        "agents": len(_state.agents),
        "concurrency": _state.concurrency_controller.get_status(),
        "kg_stats": _state.knowledge_graph.stats(),
        # #3: side-agent 降级计数(degrade 否则静默)。#1: 生效 timeout。
        "degraded_stats": dict(_state.degraded_stats),
        "sidellm_timeout": _state.SIDELLM_TIMEOUT,
        # idle-trigger 可观测:写入水位 / 各层最后触发时间 / 提炼积压(origin=AGENT
        # 未提炼数)。直接读 watcher 快照(实时),未装配时为 None。
        "maintenance": (
            await _state.db_watcher.maintenance_snapshot()
            if _state.db_watcher is not None else None
        ),
        # #4: 双 LLM 通道可观测(main=主对话, side=side agent)。
        "llm_channels": {
            "main": {"format": getattr(main_c, "format", None), "model": main_model},
            "side": ({"format": getattr(side_c, "format", None),
                      "model": getattr(side_c, "default_model", None)}
                     if side_c is not None else None),
        },
    }
