"""Memory, Permission, and Communication API routes."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Query

from src.api.models import (
    StoreMemoryRequest,
    GrantPermissionRequest,
    SendMessageRequest,
    MemoryNotifyRequest,
    MemoryConsolidateRequest,
)
from src.memory import MemoryType, MemoryScope
from src.services import _state
from src.communication.message import AgentMessage, MessageType, MessagePriority

router = APIRouter()


# ── Memory API ─────────────────────────────────────────────────────


@router.post("/memories")
async def store_memory(req: StoreMemoryRequest) -> dict:
    """Store a new memory item."""
    ref = await _state.memory_service.store(
        content=req.content,
        agent_id=req.agent_id,
        session_id=req.session_id,
        memory_type=MemoryType(req.memory_type),
        scope=MemoryScope(req.scope),
        importance=req.importance,
    )
    return {"id": ref.id, "memory_type": ref.memory_type.value, "scope": ref.scope.value}


@router.get("/memories")
async def list_memories(
    agent_id: str = "",
    session_id: str = "",
    memory_type: str = "",
    limit: int = Query(default=100, le=500),
) -> list[dict]:
    """List memories with optional filters."""
    items = await _state.memory_service.recall(
        query="",
        agent_id=agent_id,
        session_id=session_id,
        memory_type=MemoryType(memory_type) if memory_type else None,
        top_k=limit,
    )
    return [
        {
            "id": m.id,
            "agent_id": m.agent_id,
            "session_id": m.session_id,
            "memory_type": m.memory_type.value,
            "scope": m.scope.value,
            "content": m.content,
            "importance": m.importance,
            "created_at": m.created_at,
            "archived": m.archived,
        }
        for m in items
    ]


@router.get("/memories/layers")
async def memory_layers(agent_id: str = "") -> dict:
    """Get memory layer statistics for an agent."""
    items = await _state.memory_service.recall(
        query="",
        agent_id=agent_id,
        top_k=1000,
    )
    stats: dict[str, int] = {"working": 0, "session": 0, "episodic": 0, "semantic": 0}
    for m in items:
        if not m.archived and m.memory_type.value in stats:
            stats[m.memory_type.value] += 1
    return stats


@router.delete("/memories/{memory_id}")
async def delete_memory(memory_id: str) -> dict:
    """Delete a memory item."""
    deleted = await _state.memory_service.delete(memory_id)
    return {"deleted": deleted}


# ── External Maintenance API ───────────────────────────────────────


@router.post("/memory/notify")
async def notify_maintenance(req: MemoryNotifyRequest) -> dict:
    """Trigger deterministic maintenance (zero LLM) on demand.

    For external apps sharing the memories DB: call this after writing to the
    ``memories`` table to immediately run prune → forget → migrate (the SAME
    chain as the 60s poll). Only touches ``origin=AGENT`` memories; FOREGROUND
    (user / external-app-authored) memories are P0-protected and never
    auto-touched. Set ``force=True`` to run unconditionally (bypasses the
    updated_at change check).
    """
    if _state.db_watcher is None:
        return {"status": "unavailable", "results": []}
    agent_ids = [req.agent_id] if req.agent_id else list(_state.agents.keys())
    results: list[dict] = []
    for aid in agent_ids:
        try:
            if req.force:
                run = True
            else:
                changed = await asyncio.to_thread(_state.db_watcher.has_external_changes)
                run = changed is not None
            if run:
                r = await _state.db_watcher.run_maintenance(agent_id=aid, emit=True, trigger="notify")
                results.append(r)
            else:
                results.append({"agent_id": aid, "status": "noop", "reason": "no_external_changes"})
        except Exception as exc:
            results.append({"agent_id": aid, "status": "error", "error": str(exc)})
    processed = sum(1 for r in results if r.get("status") == "ok")
    return {"status": "ok", "processed": processed, "results": results}


@router.post("/memory/consolidate")
async def consolidate_memory(req: MemoryConsolidateRequest) -> dict:
    """Trigger LLM consolidation on demand (reuses ``task_consolidator``).

    Extracts key decisions / pitfalls from ``messages`` and writes them back
    via BackwardWriter. Timeout is clamped to 8s; degrades to a heuristic
    EPISODIC summary on LLM failure. Rejects empty ``messages`` (would write
    junk) and no-ops when no LLM is configured (avoids memory pollution).
    """
    if _state.task_consolidator is None:
        return {"status": "unavailable"}
    if not req.messages:
        return {"triggered": False, "written": False, "error": "messages_required"}
    llm = _state.llm_client
    if llm is not None and not getattr(llm, "api_key", None) and not getattr(llm, "anthropic_api_key", None):
        return {"triggered": False, "degraded": True, "written": False, "error": "llm_unconfigured"}
    timeout = min(float(req.timeout or 8.0), 8.0)
    try:
        result = await _state.task_consolidator.consolidate_task(
            agent_id=req.agent_id,
            session_id=req.session_id,
            messages=req.messages,
            timeout=timeout,
        )
        return {
            "triggered": result.triggered,
            "degraded": getattr(result, "degraded", False),
            "confidence": result.confidence,
            "written": result.written,
            "memory_id": result.memory_id,
            "channel": result.channel,
            "error": getattr(result, "error", None) or None,
        }
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


# ── Permission API ─────────────────────────────────────────────────


@router.post("/permissions/grant")
async def grant_permission(req: GrantPermissionRequest) -> dict:
    """Grant memory access permission between agents."""
    grant_id = _state.memory_service.grant_access(
        grantor_id=req.grantor_id,
        grantee_id=req.grantee_id,
        target_agent_id=req.target_agent_id,
        level=req.level,
        expires_at=req.expires_at,
    )
    return {"grant_id": grant_id, "level": req.level}


@router.get("/permissions/log")
async def permission_log(
    accessor_id: str = "",
    target_agent_id: str = "",
    limit: int = Query(default=50, le=500),
) -> list[dict]:
    """Get memory access log."""
    entries = _state.memory_service.get_access_log(
        accessor_id=accessor_id,
        target_agent_id=target_agent_id,
        limit=limit,
    )
    return [
        {
            "id": e.id,
            "accessor_id": e.accessor_id,
            "target_agent_id": e.target_agent_id,
            "memory_id": e.memory_id,
            "action": e.action,
            "level_granted": e.level_granted.value,
            "granted_at": e.granted_at,
        }
        for e in entries
    ]


# ── Communication API ─────────────────────────────────────────────


@router.post("/messages")
async def send_message(req: SendMessageRequest) -> dict:
    """Send a message between agents."""
    msg = AgentMessage(
        sender_id=req.sender_id,
        recipient_id=req.recipient_id,
        session_id=req.session_id,
        workspace_id=req.workspace_id,
        content=req.content,
        message_type=MessageType(req.message_type),
        priority=MessagePriority(req.priority),
    )

    if req.recipient_id:
        msg_id = await _state.communication_bus.send(msg)
    else:
        ids = await _state.communication_bus.broadcast(msg, session_id=req.session_id)
        return {"broadcast": True, "delivered_ids": ids, "count": len(ids)}

    return {"id": msg_id, "status": "delivered"}


@router.get("/messages")
async def list_messages(
    agent_id: str = "",
    session_id: str = "",
    limit: int = Query(default=50, le=500),
) -> list[dict]:
    """Get message history for an agent or session."""
    if agent_id:
        history = _state.communication_bus.get_history(agent_id, limit=limit)
        return [m.to_dict() for m in history]
    return []
