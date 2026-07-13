"""Primitive API routes — the thin orchestration layer (spec section 4).

Six endpoints over {claw, claude-code}:
- POST   /h/{type}/sessions              create session
- GET    /h/{type}/sessions              list sessions
- POST   /h/{type}/sessions/{id}/turn    trigger turn (core primitive)
- POST   /h/{type}/sessions/{id}/spawn   spawn instance (claude multi-instance)
- DELETE /h/{type}/sessions/{id}         close session
- POST   /switch                         switch active session (focus, not lock)

No locks (ADR-5): concurrency is delegated to the harness (claw gateway handles
multi-session; claude multi-PTY --resume). orchestrator only routes + observes.

Per ADR-4: orchestrator is the ONLY harness client. Each route drives the
harness client (openclaw send_message / claude spawn), and the client
background-connects + maps events → observe /ws/ingest.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .openclaw import OpenClawClient
from .claude import ClaudeClient

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/h", tags=["harness"])

VALID_TYPES = {"claw", "claude-code"}

# active session (frontend focus = send target, NOT a lock)
_active: Dict[str, str] = {"type": "", "id": ""}


# ── session registry ──────────────────────────────────────────────────
# keyed by (harness_type, session_id). Holds the live client + metadata.
_sessions: Dict[str, Dict[str, Any]] = {}


def _key(harness_type: str, session_id: str) -> str:
    return f"{harness_type}:{session_id}"


def _get_client(harness_type: str, session_id: str) -> Optional[Any]:
    rec = _sessions.get(_key(harness_type, session_id))
    return rec["client"] if rec else None


# ── request models ────────────────────────────────────────────────────

class CreateSessionReq(BaseModel):
    agent_id: Optional[str] = None   # claw agent key (e.g. agent:main:main)
    cwd: Optional[str] = None        # claude working directory


class TurnReq(BaseModel):
    message: str
    agent_id: Optional[str] = None   # claw agentId override
    thinking: Optional[str] = None   # claw thinking param
    resume: bool = False             # claude --resume path


class SwitchReq(BaseModel):
    type: str
    id: str


# ── helpers ───────────────────────────────────────────────────────────

def _validate_type(harness_type: str) -> None:
    if harness_type not in VALID_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid type '{harness_type}'. Must be one of {sorted(VALID_TYPES)}",
        )


async def _create_claw(session_id: str, agent_id: Optional[str]) -> OpenClawClient:
    client = OpenClawClient(session_key=session_id or agent_id or "agent:main:main")
    client.start_background()
    return client


async def _create_claude(session_id: str, cwd: Optional[str]) -> ClaudeClient:
    from pathlib import Path
    from .claude import DEFAULT_CWD
    client = ClaudeClient(
        session_id=session_id,
        cwd=Path(cwd) if cwd else DEFAULT_CWD,
    )
    await client.connect()
    return client


# ── routes ────────────────────────────────────────────────────────────

@router.post("/{harness_type}/sessions")
async def create_session(
    harness_type: str, req: CreateSessionReq,
) -> Dict[str, Any]:
    _validate_type(harness_type)
    if harness_type == "claw":
        # claw gateway session_key 必须是 claw 格式 agent:<agent>:<conv>;或che session_id = claw key
        # (统一,前端查 observe 同 key;uuid hex claw 不认 → subscribe/send 到不存在 session → 0 events)
        agent = req.agent_id or "main"
        session_id = agent if ":" in agent else f"agent:{agent}:main"
    else:
        session_id = str(uuid.uuid4().hex[:12])

    # ADR-4:同 session_id 复用 client,防重复订阅(多 OpenClawClient 连同 claw session 抢事件)
    key = _key(harness_type, session_id)
    if key in _sessions:
        return {"session_id": session_id, "type": harness_type, "status": "exists"}

    client = await (_create_claw(session_id, req.agent_id) if harness_type == "claw"
                    else _create_claude(session_id, req.cwd))
    _sessions[key] = {
        "client": client,
        "session_id": session_id,
        "harness_type": harness_type,
        "agent_id": req.agent_id,
    }
    return {"session_id": session_id, "type": harness_type, "status": "created"}


@router.get("/{harness_type}/sessions")
async def list_sessions(harness_type: str) -> Dict[str, Any]:
    _validate_type(harness_type)
    items = [
        {"session_id": v["session_id"], "agent_id": v.get("agent_id")}
        for v in _sessions.values()
        if v["harness_type"] == harness_type
    ]
    return {"type": harness_type, "sessions": items, "count": len(items)}


@router.post("/{harness_type}/sessions/{session_id}/turn")
async def trigger_turn(
    harness_type: str, session_id: str, req: TurnReq,
) -> Dict[str, Any]:
    _validate_type(harness_type)
    client = _get_client(harness_type, session_id)
    if client is None:
        raise HTTPException(status_code=404, detail="session not found")

    if harness_type == "claw":
        if not getattr(client, "running", False):
            raise HTTPException(status_code=503, detail="claw client not connected yet")
        await client.send_message(req.message, agent_id=req.agent_id, thinking=req.thinking)
        return {"session_id": session_id, "status": "sent", "message": req.message[:50]}
    else:  # claude-code
        result = await client.turn(req.message, resume=req.resume)
        return {"session_id": session_id, "status": result["status"],
                "tick_id": result["tick_id"]}


@router.post("/{harness_type}/sessions/{session_id}/spawn")
async def spawn_instance(
    harness_type: str, session_id: str,
) -> Dict[str, Any]:
    _validate_type(harness_type)
    client = _get_client(harness_type, session_id)
    if client is None:
        raise HTTPException(status_code=404, detail="session not found")

    if harness_type == "claw":
        # claw multi-session = create another session on the same agent
        raise HTTPException(
            status_code=400,
            detail="claw multi-session: POST a new /h/claw/sessions (same agent_id)",
        )
    return await client.spawn_instance()


@router.delete("/{harness_type}/sessions/{session_id}")
async def delete_session(
    harness_type: str, session_id: str,
) -> Dict[str, Any]:
    _validate_type(harness_type)
    k = _key(harness_type, session_id)
    rec = _sessions.pop(k, None)
    if rec is None:
        raise HTTPException(status_code=404, detail="session not found")
    try:
        await rec["client"].stop()
    except Exception as e:
        logger.warning("session stop error: %s", e)
    # clear active if it pointed here
    if _active["type"] == harness_type and _active["id"] == session_id:
        _active.update(type="", id="")
    return {"session_id": session_id, "status": "deleted"}


# ── flow engine (P2: turn chains / branches / DAG on trigger_turn) ────
# FlowDef JSON DSL → scheduler runs nodes over the existing turn primitive.
# 挂在 /h/flows(*):与 session primitive 同 router(/h prefix)。

from .flow import FlowDef, FlowScheduler, get_flow, _flows as _flow_registry

# flow_id → {def, state, scheduler, task}
# (_flow_registry in flow.py is the source of truth; this aliases for clarity)


@router.post("/flows")
async def create_flow(req: FlowDef) -> Dict[str, Any]:
    """Create a flow from a FlowDef. Validates the graph, returns flow_id."""
    req.validate_graph()
    flow_id = f"flow_{uuid.uuid4().hex[:12]}"
    scheduler = FlowScheduler(req, flow_id)
    _flow_registry[flow_id] = {
        "def": req, "state": scheduler.state, "scheduler": scheduler,
        "task": None,
    }
    return {"flow_id": flow_id, "status": "created",
            "nodes": [n.id for n in req.nodes],
            "edges": [{"from": e.from_, "to": e.to} for e in req.edges]}


@router.post("/flows/{flow_id}/run")
async def run_flow(flow_id: str) -> Dict[str, Any]:
    """Asynchronously execute a flow. Returns immediately; observe receives
    flow_started/node_started/node_completed/flow_completed events."""
    rec = get_flow(flow_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="flow not found")
    scheduler: FlowScheduler = rec["scheduler"]
    if rec.get("task") is not None and not rec["task"].done():
        raise HTTPException(status_code=409, detail="flow already running")
    task = scheduler.start_background()
    rec["task"] = task
    return {"flow_id": flow_id, "status": "running"}


@router.get("/flows/{flow_id}")
async def get_flow_status(flow_id: str) -> Dict[str, Any]:
    """Flow + per-node status."""
    rec = get_flow(flow_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="flow not found")
    return rec["state"].to_dict()


# ── switch (no prefix — mounted at app root as /switch) ───────────────
# Exposed via a separate include so it sits at POST /switch, not /h/switch.

switch_router = APIRouter(tags=["harness"])


@switch_router.post("/switch")
async def switch_session(req: SwitchReq) -> Dict[str, Any]:
    _validate_type(req.type)
    if _key(req.type, req.id) not in _sessions:
        raise HTTPException(status_code=404, detail="session not found")
    _active.update(type=req.type, id=req.id)
    return {"active": dict(_active), "status": "switched"}
