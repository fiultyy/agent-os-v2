"""P0-6: Canvas WebSocket + REST routes.

Provides:
- WS ``/ws/canvas`` — real-time event stream (subscribe, replay, live events)
- REST ``/api/canvas/branch/create`` — fork a new branch
- REST ``/api/canvas/branch/merge`` — merge a branch back
- REST ``/api/canvas/branch/prune`` — discard a branch

All state is wired through the canvas module's singletons.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field, field_validator

from src.canvas.branch import Branch, BranchStatus, BranchStore
from src.canvas.events import (
    BranchCreatedEvent,
    BranchMergedEvent,
)
from src.canvas.event_store import CanvasEventStore
from src.canvas.emitter import SessionEventEmitter
from src.canvas.tab_manager import TabManager

logger = logging.getLogger(__name__)

router = APIRouter()

# ── Module-level singletons (initialized by app lifespan) ──────────
# These are set once during application startup.

_store: Optional[CanvasEventStore] = None
_emitter: Optional[SessionEventEmitter] = None
_tab_manager: Optional[TabManager] = None

# Branch registry (persistent via SQLite)
_branch_store: Optional[BranchStore] = None

# ── Auth configuration ────────────────────────────────────────

# Canvas API token — set via CANVAS_API_TOKEN env var.
# Falls back to a development default when not set.
_CANVAS_API_TOKEN: str = os.environ.get("CANVAS_API_TOKEN", "")

# Allowed origins for WebSocket connections (Origin header check).
# Comma-separated list. Empty string = allow all (dev mode).
_ALLOWED_ORIGINS: list[str] = [
    o.strip()
    for o in os.environ.get("CANVAS_ALLOWED_ORIGINS", "").split(",")
    if o.strip()
]


def _verify_token(token: str) -> bool:
    """Check the provided token against the configured API token.

    Returns True if authentication passes.  When ``CANVAS_API_TOKEN``
    is not set (empty string), authentication is disabled for
    backwards-compatible development usage.
    """
    if not _CANVAS_API_TOKEN:
        # No token configured — auth disabled (dev mode)
        return True
    # Constant-time comparison to prevent timing attacks
    import hmac
    return hmac.compare_digest(token, _CANVAS_API_TOKEN)


def _verify_origin(origin: str | None) -> bool:
    """Check the Origin header against allowed origins.

    When ``CANVAS_ALLOWED_ORIGINS`` is empty, only localhost origins
    are allowed (localhost / 127.0.0.1 / [::1]).
    Set the env var in production.
    Special regex characters in *origin* are escaped to prevent ReDoS.
    """
    if not origin:
        return False
    if _ALLOWED_ORIGINS:
        safe_origin = re.escape(origin)
        for allowed in _ALLOWED_ORIGINS:
            if re.fullmatch(allowed.replace("*", ".*"), safe_origin):
                return True
        return False
    # No explicit origins configured — allow localhost only.
    safe_origin = re.escape(origin)
    return bool(re.fullmatch(
        r"(https?://)?(localhost|127\.0\.0\.1|\[::1\])(:\d+)?(/.*)?",
        safe_origin,
    ))


def init_canvas_routes(
    store: CanvasEventStore,
    emitter: SessionEventEmitter,
    tab_manager: TabManager,
) -> None:
    """Wire up dependencies. Called once during app startup."""
    global _store, _emitter, _tab_manager, _branch_store
    _store = store
    _emitter = emitter
    _tab_manager = tab_manager
    _branch_store = BranchStore()


# ── Pydantic request models (W-5) ────────────────────────────

class CreateBranchRequest(BaseModel):
    session_id: str = Field(..., min_length=1, description="Canvas session ID")
    parent_branch_id: str = Field("main", min_length=1, description="Parent branch to fork from")
    fork_tick_id: str = Field("", description="Optional tick ID to fork at")


class MergeBranchRequest(BaseModel):
    branch_id: str = Field(..., min_length=1, description="Branch to merge")
    target_branch_id: str = Field("main", min_length=1, description="Target branch")
    session_id: Optional[str] = Field(None, description="Session ID for audit logging")


class PruneBranchRequest(BaseModel):
    branch_id: str = Field(..., min_length=1, description="Branch to prune")
    session_id: Optional[str] = Field(None, description="Session ID for audit logging")


# ── WebSocket endpoint ─────────────────────────────────────────────


@router.websocket("/ws/canvas")
async def canvas_websocket(
    ws: WebSocket,
    session_id: str = Query("default"),
) -> None:
    """WebSocket endpoint for live canvas events.

    Protocol:
    1. Client connects with ``?session_id=xxx&token=yyy``
    2. Server validates token and Origin header
    3. Server replays historical events
    4. Server pushes live events in real-time
    5. Client can send JSON commands:
       - ``{"cmd": "switch_branch", "branch_id": "xxx"}``
       - ``{"cmd": "ping"}``
    """
    # ── C-2: Authentication ────────────────────────────────
    token = ws.query_params.get("token", "")

    if not _verify_token(token):
        await ws.close(code=4001, reason="authentication failed")
        logger.warning("Canvas WS auth failed: session=%s", session_id)
        return

    # ── C-2: Origin check ──────────────────────────────────
    origin = ws.headers.get("origin")
    if not _verify_origin(origin):
        await ws.close(code=4003, reason="origin not allowed")
        logger.warning("Canvas WS origin rejected: origin=%s session=%s", origin, session_id)
        return

    # ── C-2: session_id validation ─────────────────────────
    if not session_id or session_id == "default":
        await ws.close(code=4002, reason="session_id required")
        return

    await ws.accept()

    if _emitter is None or _tab_manager is None:
        await ws.close(code=1011, reason="Canvas not initialized")
        return

    # Register tab
    tab = _tab_manager.create_tab(session_id=session_id)
    await _emitter.subscribe(session_id, ws)

    # Replay history
    await _emitter.replay(session_id, ws)

    logger.info("Canvas WS connected: session=%s tab=%s", session_id, tab.tab_id)

    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await ws.send_text(json.dumps({"error": "invalid_json"}))
                continue

            cmd = msg.get("cmd", "")

            if cmd == "ping":
                await ws.send_text(json.dumps({"cmd": "pong"}))

            elif cmd == "subscribe":
                # Client re-subscribes (idempotent — already subscribed at connect).
                await ws.send_text(json.dumps({
                    "cmd": "subscribed",
                    "session_id": session_id,
                    "tab_id": tab.tab_id,
                }))

            elif cmd == "switch_branch":
                new_branch_id = msg.get("branch_id", "main")
                if not new_branch_id:
                    await ws.send_text(json.dumps({"error": "branch_id required"}))
                    continue
                _tab_manager.switch_branch(tab.tab_id, new_branch_id)
                # Replay events for new branch
                if _store:
                    events = _store.get_branch_ticks(new_branch_id)
                    for ev in events:
                        await ws.send_text(json.dumps(ev, ensure_ascii=False))
                await ws.send_text(json.dumps({
                    "cmd": "branch_switched",
                    "branch_id": new_branch_id,
                }))

            elif cmd == "replay":
                after_id = msg.get("after_event_id")
                await _emitter.replay(session_id, ws, after_id)

            elif cmd == "layer2.submit":
                # Validate required fields
                nodes = msg.get("nodes")
                commands = msg.get("commands")
                if not nodes and not commands:
                    await ws.send_text(json.dumps({"error": "layer2.submit requires 'nodes' or 'commands'"}))
                    continue
                # Emit event for downstream processing
                if _emitter:
                    from src.canvas.events import CanvasEvent as CE
                    evt = CE(
                        session_id=session_id,
                        branch_id=_tab_manager.get_branch_for_tab(tab.tab_id) or "main",
                        tick_id="",
                        event_type="layer2.submitted",
                        data={"nodes": nodes or [], "commands": commands or []},
                        lod=2,
                    )
                    await _emitter.emit(evt)
                await ws.send_text(json.dumps({"cmd": "layer2.submitted", "status": "accepted"}))

            else:
                await ws.send_text(json.dumps({"error": f"unknown cmd: {cmd}"}))

    except WebSocketDisconnect:
        logger.info("Canvas WS disconnected: session=%s tab=%s", session_id, tab.tab_id)
    except Exception as exc:
        logger.error("Canvas WS error: %s", exc)
    finally:
        await _emitter.unsubscribe(session_id, ws)
        _tab_manager.close_tab(tab.tab_id)


# ── REST endpoints ─────────────────────────────────────────────────


@router.post("/api/canvas/branch/create")
async def create_branch(
    req: CreateBranchRequest,
) -> dict:
    """Fork a new branch from an existing branch at a tick."""
    branch = Branch.fork(
        session_id=req.session_id,
        parent_branch_id=req.parent_branch_id,
        fork_tick_id=req.fork_tick_id,
    )
    _branch_store.save(branch)

    # Emit event
    if _emitter:
        event = BranchCreatedEvent.create(
            session_id=req.session_id,
            branch_id=branch.branch_id,
            parent_branch_id=req.parent_branch_id,
            fork_tick_id=req.fork_tick_id,
        )
        await _emitter.emit(event)

    return branch.to_dict()


@router.post("/api/canvas/branch/merge")
async def merge_branch(
    req: MergeBranchRequest,
) -> dict:
    """Merge a branch back into its parent."""
    branch = _branch_store.get(req.branch_id)
    if branch is None:
        raise HTTPException(status_code=404, detail=f"branch not found: {req.branch_id}")
    if branch.status != BranchStatus.ACTIVE:
        raise HTTPException(status_code=409, detail=f"branch is {branch.status.value}, cannot merge")

    branch.status = BranchStatus.MERGED
    from datetime import datetime, timezone
    branch.merged_at = datetime.now(timezone.utc).isoformat()

    # Emit event
    if _emitter:
        event = BranchMergedEvent.create(
            session_id=branch.session_id,
            branch_id=req.branch_id,
            target_branch_id=req.target_branch_id,
        )
        await _emitter.emit(event)

    return branch.to_dict()


@router.post("/api/canvas/branch/prune")
async def prune_branch(
    req: PruneBranchRequest,
) -> dict:
    """Discard (prune) a branch. Events are retained for audit."""
    branch = _branch_store.get(req.branch_id)
    if branch is None:
        raise HTTPException(status_code=404, detail=f"branch not found: {req.branch_id}")
    if branch.branch_id == "main":
        raise HTTPException(status_code=409, detail="cannot prune main branch")

    branch.status = BranchStatus.PRUNED
    _branch_store.save(branch)

    return branch.to_dict()
