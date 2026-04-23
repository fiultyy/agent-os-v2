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
from typing import Optional

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from src.canvas.branch import Branch, BranchStatus
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

# In-memory branch registry (branch_id -> Branch)
_branches: dict[str, Branch] = {}


def init_canvas_routes(
    store: CanvasEventStore,
    emitter: SessionEventEmitter,
    tab_manager: TabManager,
) -> None:
    """Wire up dependencies. Called once during app startup."""
    global _store, _emitter, _tab_manager
    _store = store
    _emitter = emitter
    _tab_manager = tab_manager


# ── WebSocket endpoint ─────────────────────────────────────────────


@router.websocket("/ws/canvas")
async def canvas_websocket(
    ws: WebSocket,
    session_id: str = Query("default"),
) -> None:
    """WebSocket endpoint for live canvas events.

    Protocol:
    1. Client connects with ``?session_id=xxx``
    2. Server replays historical events
    3. Server pushes live events in real-time
    4. Client can send JSON commands:
       - ``{"cmd": "switch_branch", "branch_id": "xxx"}``
       - ``{"cmd": "ping"}``
    """
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

            elif cmd == "switch_branch":
                new_branch_id = msg.get("branch_id", "main")
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
                await _emitter.replay(session_id, ws, after_event_id)

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
    session_id: str = Query(...),
    parent_branch_id: str = Query("main"),
    fork_tick_id: str = Query(""),
) -> dict:
    """Fork a new branch from an existing branch at a tick."""
    branch = Branch.fork(
        session_id=session_id,
        parent_branch_id=parent_branch_id,
        fork_tick_id=fork_tick_id,
    )
    _branches[branch.branch_id] = branch

    # Emit event
    if _emitter:
        event = BranchCreatedEvent.create(
            session_id=session_id,
            branch_id=branch.branch_id,
            parent_branch_id=parent_branch_id,
            fork_tick_id=fork_tick_id,
        )
        await _emitter.emit(event)

    return branch.to_dict()


@router.post("/api/canvas/branch/merge")
async def merge_branch(
    branch_id: str = Query(...),
    target_branch_id: str = Query("main"),
) -> dict:
    """Merge a branch back into its parent."""
    branch = _branches.get(branch_id)
    if branch is None:
        return {"error": "branch not found", "branch_id": branch_id}
    if branch.status != BranchStatus.ACTIVE:
        return {"error": f"branch is {branch.status.value}, cannot merge"}

    branch.status = BranchStatus.MERGED
    from datetime import datetime, timezone
    branch.merged_at = datetime.now(timezone.utc).isoformat()

    # Emit event
    if _emitter:
        event = BranchMergedEvent.create(
            session_id=branch.session_id,
            branch_id=branch_id,
            target_branch_id=target_branch_id,
        )
        await _emitter.emit(event)

    return branch.to_dict()


@router.post("/api/canvas/branch/prune")
async def prune_branch(
    branch_id: str = Query(...),
) -> dict:
    """Discard (prune) a branch. Events are retained for audit."""
    branch = _branches.get(branch_id)
    if branch is None:
        return {"error": "branch not found", "branch_id": branch_id}
    if branch.branch_id == "main":
        return {"error": "cannot prune main branch"}

    branch.status = BranchStatus.PRUNED

    return branch.to_dict()
