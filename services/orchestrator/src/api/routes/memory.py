"""Memory, Permission, and Communication API routes."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Query

from src.api.models import (
    StoreMemoryRequest,
    GrantPermissionRequest,
    SendMessageRequest,
    MemoryNotifyRequest,
    MemoryConsolidateRequest,
)
from src.memory import MemoryType, MemoryScope
from src.memory.event_bus import EventType
from src.memory.hooks import (
    ConsolidateContext,
    CurateContext,
    IngestContext,
    RecallContext,
)
from src.memory.types import MemoryOrigin
from src.services import _state
from src.communication.message import AgentMessage, MessageType, MessagePriority

router = APIRouter()


# ── Memory API ─────────────────────────────────────────────────────


@router.post("/memories")
async def store_memory(req: StoreMemoryRequest) -> dict:
    """Store a new memory item.

    When ``sync_extract`` is True the route awaits the ① IngestorAgent LLM
    extraction (via ``EventType.INGEST``) and returns the
    entities/identity_category the agent produced. When False (default) the
    store returns immediately and ingestion is fire-and-forget.
    """
    ref = await _state.memory_service.store(
        content=req.content,
        agent_id=req.agent_id,
        session_id=req.session_id,
        memory_type=MemoryType(req.memory_type),
        scope=MemoryScope(req.scope),
        importance=req.importance,
    )

    # The store endpoint receives user-authored content → origin=FOREGROUND.
    # The IngestorAgent P0 red-line returns early on FOREGROUND, so emitting
    # INGEST here is only meaningful when the caller knows the content is
    # agent-self-sedimented (an external harness mirroring an agent's output).
    ingest_extras: dict[str, Any] = {}
    if req.sync_extract:
        ctx = IngestContext(
            memory_id=ref.id,
            content=req.content,
            agent_id=req.agent_id,
            session_id=req.session_id,
            origin=MemoryOrigin.AGENT.value,
        )
        result = await _state.memory_event_bus.emit(EventType.INGEST, ctx)
        if result is not None:
            ingest_extras = {
                "entities_added": getattr(result, "entities_added", 0),
                "relations_added": getattr(result, "relations_added", 0),
                "importance": getattr(result, "importance", None),
                "identity_category": getattr(result, "identity_category", "NONE"),
                "degraded": getattr(result, "degraded", False),
            }

    resp: dict[str, Any] = {
        "id": ref.id,
        "memory_type": ref.memory_type.value,
        "scope": ref.scope.value,
    }
    resp.update(ingest_extras)
    return resp


def _mem_to_dict(m: Any, score: float | None = None) -> dict:
    d = {
        "id": m.id,
        "agent_id": m.agent_id,
        "session_id": m.session_id,
        "memory_type": m.memory_type.value,
        "scope": m.scope.value,
        "content": m.content,
        "importance": m.importance,
        "created_at": m.created_at,
        "archived": m.archived,
        # origin: foreground(用户/外部 harness 保护写) vs agent(自主沉淀)。
        # P0 provenance 透出,让消费端区分"用户保护记忆 vs agent 沉淀"。
        "origin": m.origin.value,
        # state: P3 确定性生命周期(active/stale/archived)。getattr 防御未带 state 的旧对象,
        # 让消费端识别 STALE 过期记忆。
        "state": m.state.value if getattr(m, "state", None) is not None else None,
    }
    if score is not None:
        d["score"] = score
    return d


@router.get("/memories")
async def list_memories(
    agent_id: str = "",
    session_id: str = "",
    memory_type: str = "",
    scope: str = "",
    query: str = "",
    sort: str = "",
    top_k: int = Query(default=100, le=500, alias="limit"),
) -> list[dict]:
    """List / recall memories.

    Review correction (#4, high): recall routing lives HERE in the route
    layer — it calls ``bus.emit(RECALL)`` (③ RetrieverAgent) and falls back
    to plain ``service.recall`` when no OBSERVER hook is wired. The service
    layer is never reached into from a side agent.

    - ``query``: when non-empty, runs the ③ RetrieverAgent match-scoring
      path (returns ``{..., "score"}`` sorted by score desc). When the
      retriever is disabled (no RECALL hook), falls back to
      ``service.recall(query)``.
    - ``sort=importance``: order the (un-queried) full list by importance
      desc — previously ``GET /memories`` was hardcoded ``query=""`` with no
      ordering, leaving the internal recall capability unexposed.
    - ``top_k`` (alias ``limit``): result cap.
    """
    if query:
        # Route through the ③ RetrieverAgent hook (OBSERVER). Returns None
        # when the feature gate is off → fall back to plain service.recall.
        ctx = RecallContext(
            query=query, agent_id=agent_id, session_id=session_id, scope=scope, top_k=top_k,
        )
        ranked = await _state.memory_event_bus.emit(EventType.RECALL, ctx)
        if ranked is not None:
            return [
                _mem_to_dict(r["item"], score=r["score"]) for r in ranked
            ]
        items = await _state.memory_service.recall(
            query=query,
            agent_id=agent_id,
            session_id=session_id,
            memory_type=MemoryType(memory_type) if memory_type else None,
            scope=MemoryScope(scope) if scope else None,
            top_k=top_k,
        )
        return [_mem_to_dict(m) for m in items]

    items = await _state.memory_service.recall(
        query="",
        agent_id=agent_id,
        session_id=session_id,
        memory_type=MemoryType(memory_type) if memory_type else None,
        scope=MemoryScope(scope) if scope else None,
        top_k=top_k,
    )
    if sort == "importance":
        items = sorted(items, key=lambda m: m.importance, reverse=True)
    return [_mem_to_dict(m) for m in items]


@router.get("/identity")
async def identity(agent_id: str = "") -> dict:
    """Identity-recall closure (Chapter 6.2).

    Returns four memory groups keyed by ``identity_category`` plus the neural
    field's ``attention_group`` and ``baseline`` (personality, slow-varying).
    Programmatic only — NO LLM synthesis here. Per design §6.3, emergent
    self-narration is the REQUESTER's concern; this endpoint only delivers
    the memory groups + score. The four buckets:

      - what_i_remember: attention_group + KNOWLEDGE-tagged top
      - who_am_i:        IDENTITY-tagged memories
      - my_goals:        GOAL-tagged memories
      - my_traits:       TRAIT-tagged memories
      - personality:     neural baseline (LIF slow-varying sediment)
    """
    if not agent_id:
        agent_id = next(iter(_state.agents.keys()), "")

    out: dict[str, Any] = {
        "agent_id": agent_id,
        "what_i_remember": [],
        "who_am_i": [],
        "my_goals": [],
        "my_traits": [],
        "personality": {},
    }

    # identity_category is stored in item metadata by ① IngestorAgent.
    # Bucket the agent's non-archived memories by their tag.
    try:
        items = await _state.memory_service.recall(
            query="", agent_id=agent_id, top_k=500
        )
    except Exception:
        items = []

    buckets = {
        "IDENTITY": "who_am_i",
        "GOAL": "my_goals",
        "TRAIT": "my_traits",
        "KNOWLEDGE": "what_i_remember",
    }
    knowledge_items: list[Any] = []
    for m in items:
        if getattr(m, "archived", False):
            continue
        meta = getattr(m, "metadata", {}) or {}
        cat = str(meta.get("identity_category", "NONE")).upper()
        target_key = buckets.get(cat)
        if target_key is None:
            continue
        out[target_key].append(_mem_to_dict(m))
        if cat == "KNOWLEDGE":
            knowledge_items.append(m)

    # what_i_remember = attention_group (neural field projection) + KNOWLEDGE.
    attention_ids: list[str] = []
    if _state.neural_store is not None:
        try:
            state = _state.neural_store.load_state(agent_id)
            if state is not None:
                attention_ids = list(state.attention_group)
                out["personality"] = dict(state.baseline)
        except Exception:
            pass

    if attention_ids:
        seen = {m["id"] for m in out["what_i_remember"]}
        for mid in attention_ids:
            if mid in seen:
                continue
            item = await _state.memory_service.get(mid)
            if item is not None and not getattr(item, "archived", False):
                out["what_i_remember"].insert(0, _mem_to_dict(item))

    return out


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

    # ?curate=true → fire a ④ CuratorAgent pass as an INDEPENDENT
    # fire-and-forget task. NEVER inserted into the synchronous
    # run_maintenance Zero-LLM chain above (review correction #11).
    curate_triggered = False
    if req.curate and _state.curator is not None:
        agent_ids = [req.agent_id] if req.agent_id else list(_state.agents.keys())
        for aid in agent_ids:
            ctx = CurateContext(agent_id=aid, scope="all")
            asyncio.create_task(_state.memory_event_bus.emit(EventType.CURATE, ctx))
        curate_triggered = bool(agent_ids)

    return {
        "status": "ok",
        "processed": processed,
        "results": results,
        "curate_triggered": curate_triggered,
    }


@router.post("/memory/consolidate")
async def consolidate_memory(req: MemoryConsolidateRequest) -> dict:
    """Trigger consolidation on demand.

    Two modes:
    - ``mode=merge`` (default-agnostic): trigger ② ConsolidatorAgent —
      episodic → semantic understanding-driven merge (LLM), distinct from
      the task-post experience sedimentation below.
    - default (``task_consolidator``): reuses TaskConsolidationAgent —
      extracts key decisions / pitfalls from ``messages`` and writes them
      via BackwardWriter. Timeout clamped to 8s; degrades to heuristic
      EPISODIC summary on LLM failure. Rejects empty ``messages``.
    """
    if req.mode == "merge":
        if _state.consolidator is None:
            return {"status": "unavailable", "reason": "consolidator_disabled"}
        agent_id = req.agent_id or next(iter(_state.agents.keys()), "")
        try:
            result = await _state.consolidator.consolidate(
                agent_id=agent_id,
                trigger="api_merge",
                top_k=20,
                timeout=min(float(req.timeout or 8.0), 8.0),
            )
            return {
                "triggered": result.triggered,
                "degraded": getattr(result, "degraded", False),
                "written": getattr(result, "written", False),
                "merged_count": getattr(result, "merged_count", 0),
                "semantic_ids": getattr(result, "semantic_ids", []),
                "archived_ids": getattr(result, "archived_ids", []),
                "error": getattr(result, "error", "") or None,
            }
        except Exception as exc:
            return {"status": "error", "error": str(exc)}

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
