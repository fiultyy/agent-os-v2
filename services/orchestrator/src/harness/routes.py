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

# observe-service REST base (sessions are persisted in SQLite there; the TUI's
# source of truth). orche delete must sync here or the count drifts.
OBSERVE_REST_URL = "http://localhost:8002"

# routes use "claw" as the harness key, but the openclaw client registers with
# observe under harness_type="openclaw". Map so the DELETE hits the right row.
_OBSERVE_HARNESS_TYPE = {"claw": "openclaw", "claude-code": "claude-code"}


async def _observe_delete_session(harness_type: str, session_id: str) -> bool:
    """Best-effort DELETE of a session record from observe-service.

    Non-fatal: if observe is unreachable the orche delete still succeeds.
    Runs the blocking http call off the event loop via asyncio.to_thread.
    """
    ob_type = _OBSERVE_HARNESS_TYPE.get(harness_type, harness_type)
    url = f"{OBSERVE_REST_URL}/sessions/{ob_type}/{session_id}"

    def _do_delete() -> bool:
        import urllib.request
        try:
            req = urllib.request.Request(url, method="DELETE")
            with urllib.request.urlopen(req, timeout=3) as resp:
                return 200 <= resp.status < 300
        except Exception as e:
            logger.warning("observe session delete failed (%s/%s): %s",
                           ob_type, session_id, e)
            return False

    return await asyncio.to_thread(_do_delete)

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
    client = rec["client"]
    # raw delete first (jsonl transcript / gateway transcript), then stop.
    # client.delete() calls stop() itself on success; fall back to bare stop
    # if the client has no delete method or raw delete fails.
    raw_deleted = False
    delete_meth = getattr(client, "delete", None)
    if delete_meth is not None:
        try:
            if harness_type == "claude-code":
                r = await delete_meth(session_id)
            else:  # claw: delete takes no args
                r = await delete_meth()
            raw_deleted = bool(r.get("deleted"))
        except Exception as e:
            logger.warning("session raw delete error: %s", e)
            try:
                await client.stop()
            except Exception as e2:
                logger.warning("session stop fallback error: %s", e2)
    else:
        try:
            await client.stop()
        except Exception as e:
            logger.warning("session stop error: %s", e)
    # clear active if it pointed here
    if _active["type"] == harness_type and _active["id"] == session_id:
        _active.update(type="", id="")
    # sync the delete to observe (its SQLite is the TUI's session source of
    # truth). best-effort: failure here does NOT fail the orche delete.
    ob_deleted = await _observe_delete_session(harness_type, session_id)
    return {"session_id": session_id, "status": "deleted",
            "raw_deleted": raw_deleted, "observe_deleted": ob_deleted}


# ── pickers (option lists for the frontend create/fork dialogs) ───────

@router.get("/claw/agents")
async def list_claw_agents() -> Dict[str, Any]:
    """List registered claw agents for the picker.

    Reads ~/.openclaw/openclaw.json:
    - channels.feishu.accounts keys → real gateway-capable agents (main, claw-02, ...).
    - acp.allowedAgents → acpx agents (claude, codex, ...).
    Returns {"agents": [...], "default": "..."}; feishu accounts first, then acp;
    falls back to ["main"] when neither is present.
    """
    import json as _json
    from pathlib import Path
    default_agents = ["main"]
    default_default = "main"
    try:
        cfg_path = Path.home() / ".openclaw" / "openclaw.json"
        with open(cfg_path) as f:
            cfg = _json.load(f) or {}
        # feishu accounts(能创 gateway session 的真实 agent)。
        feishu = (((cfg.get("channels") or {}).get("feishu")) or {})
        accts = feishu.get("accounts") or {}
        feishu_agents = list(accts.keys()) if isinstance(accts, dict) else []
        # acp allowedAgents(acpx agent)。
        acp = cfg.get("acp", {}) or {}
        acp_agents = acp.get("allowedAgents") or []
        agents = feishu_agents + [a for a in acp_agents if a not in feishu_agents]
        if not agents:
            agents = default_agents
        default = (acp.get("defaultAgent") if acp.get("defaultAgent") else None) \
            or (feishu_agents[0] if feishu_agents else None) \
            or (agents[0] if agents else default_default)
        return {"agents": agents, "default": default}
    except Exception as e:
        logger.warning("list_claw_agents: read cfg failed: %s", e)
        return {"agents": default_agents, "default": default_default}


@router.get("/claude-code/cwds")
async def list_cc_cwds() -> Dict[str, Any]:
    """List deduped cwds of registered claude-code sessions for the picker.

    Scans _sessions for harness_type == 'claude-code', collects each client's
    .cwd (Path), dedupes. Returns {"cwds": [str,...], "default": "<first or DEFAULT_CWD>"}.
    """
    from .claude import DEFAULT_CWD
    seen: list[str] = []
    for v in _sessions.values():
        if v.get("harness_type") != "claude-code":
            continue
        client = v.get("client")
        cwd = getattr(client, "cwd", None)
        if cwd is None:
            continue
        s = str(cwd)
        if s not in seen:
            seen.append(s)
    if not seen:
        return {"cwds": [str(DEFAULT_CWD)], "default": str(DEFAULT_CWD)}
    return {"cwds": seen, "default": seen[0]}


# ── fork (branch a session's context into a new session) ──────────────

class ForkReq(BaseModel):
    source_session_id: str
    first_message: str
    new_session_id: Optional[str] = None  # cc only (new sid comes from stream)


@router.post("/{harness_type}/sessions/fork")
async def fork_session(
    harness_type: str, req: ForkReq,
) -> Dict[str, Any]:
    _validate_type(harness_type)
    source = req.source_session_id
    client = _get_client(harness_type, source)
    if client is None:
        raise HTTPException(status_code=404, detail="source session not found")

    if harness_type == "claude-code":
        r = await client.fork(source, req.first_message)
        new_sid = req.new_session_id or r.get("new_sid")
        if not new_sid:
            raise HTTPException(
                status_code=500,
                detail=f"fork returned no new_sid: {r}",
            )
        # register the new session (new ClaudeClient on same cwd)
        cwd = str(getattr(client, "cwd", ""))
        new_client = await _create_claude(new_sid, cwd or None)
        _sessions[_key(harness_type, new_sid)] = {
            "client": new_client,
            "session_id": new_sid,
            "harness_type": harness_type,
            "agent_id": None,
        }
        return {"new_session_id": new_sid, "source": source,
                "forked": True, "detail": r}

    # claw: fork() is an ADR-4 stub (returns forked=False)
    r = await client.fork()
    if not r.get("forked"):
        raise HTTPException(
            status_code=501,
            detail={"forked": False, "error": r.get("error"),
                    "key": r.get("key"), "new_key": r.get("new_key")},
        )
    new_key = req.new_session_id or r.get("new_key")
    if not new_key:
        raise HTTPException(status_code=500, detail=f"fork returned no new_key: {r}")
    new_client = await _create_claw(new_key, None)
    _sessions[_key(harness_type, new_key)] = {
        "client": new_client,
        "session_id": new_key,
        "harness_type": harness_type,
        "agent_id": None,
    }
    return {"new_session_id": new_key, "source": source,
            "forked": True, "detail": r}


# ── archive (summarize a session in place) ────────────────────────────
# ADR-3: drives an existing turn primitive with a summary prompt. The
# summary lands in the session transcript + observe, same as any turn.

class ArchiveReq(BaseModel):
    prompt: Optional[str] = None


@router.post("/{harness_type}/sessions/{session_id}/archive")
async def archive_session(
    harness_type: str, session_id: str, req: ArchiveReq,
) -> Dict[str, Any]:
    _validate_type(harness_type)
    client = _get_client(harness_type, session_id)
    if client is None:
        raise HTTPException(status_code=404, detail="session not found")

    prompt = req.prompt or "用要点总结本 session 至今的事实、决策与未决项"

    if harness_type == "claw":
        if not getattr(client, "running", False):
            raise HTTPException(status_code=503, detail="claw client not connected yet")
        await client.send_message(prompt)
        return {"session_id": session_id, "status": "archived"}
    # claude-code
    result = await client.turn(prompt)
    return {"session_id": session_id, "status": "archived",
            "tick_id": result.get("tick_id")}


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
