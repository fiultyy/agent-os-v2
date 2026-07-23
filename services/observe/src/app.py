"""Observe-Service: FastAPI main application.

纯观测数据服务（ADR-2/ADR-4）：只收，不连任何 harness。
独立进程，端口 8002：
- WS ingest /ws/ingest（收 orchestrator 推）
- WS subscribe /ws/subscribe
- REST query /sessions/{harness_type}/{session_id}/events
- Session management /sessions
- Health /health

驱动能力（send/trigger）已归 orchestrator（唯一 harness 客户端，ADR-4）。
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Dict, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from src.event_store import EventStore
from src.emitter import EventEmitter
from src.events import ObserveEvent
from src.session_store import SessionStore


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ── Global State ─────────────────────────────────────────────────────

session_store: Optional[SessionStore] = None
event_store: Optional[EventStore] = None
emitter: Optional[EventEmitter] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize stores on startup, close on shutdown."""
    global session_store, event_store, emitter
    session_store = SessionStore()
    event_store = EventStore()
    emitter = EventEmitter(event_store)
    logger.info("Observe-Service started")
    yield
    # Cleanup
    session_store.close()
    event_store.close()
    logger.info("Observe-Service stopped")


app = FastAPI(
    title="Observe-Service",
    description="Multi-harness turn observation service",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS: 前端(web:3000)跨域调 observe-service(8002)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Schemas ────────────────────────────────────────────────────────

class SessionCreate(BaseModel):
    harness_type: str
    session_id: str
    harness_id: str
    agent_id: str = ""  # ADR-1: optional semantic agent_id


# ── Health ─────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok", "service": "observe-service"}


# ── Session Management ───────────────────────────────────────────────

@app.get("/sessions")
async def list_sessions(harness_type: Optional[str] = None):
    """List sessions, optionally filtered by harness_type."""
    if not session_store:
        return JSONResponse(status_code=503, content={"error": "Service not ready"})
    if harness_type:
        sessions = session_store.list_sessions(harness_type)
    else:
        sessions = session_store.list_sessions()
    return {"sessions": sessions}


@app.get("/sessions/grouped")
async def list_sessions_grouped():
    """List sessions grouped by harness_type (for UI dropdown)."""
    if not session_store:
        return JSONResponse(status_code=503, content={"error": "Service not ready"})
    grouped = session_store.list_by_harness_group()
    return {"sessions_by_harness": grouped}


@app.get("/sessions/{harness_type}/{session_id}")
async def get_session(harness_type: str, session_id: str):
    """Get session details."""
    if not session_store:
        return JSONResponse(status_code=503, content={"error": "Service not ready"})
    sess = session_store.get_session(harness_type, session_id)
    if not sess:
        return JSONResponse(status_code=404, content={"error": "Session not found"})
    return sess


@app.post("/sessions")
async def create_session(req: SessionCreate):
    """Create/register a session."""
    if not session_store:
        return JSONResponse(status_code=503, content={"error": "Service not ready"})
    session_store.create_session(
        req.harness_type,
        req.session_id,
        req.harness_id,
        agent_id=req.agent_id,
    )
    logger.info(f"Session created: {req.harness_type}/{req.session_id}")
    return {"status": "created"}


@app.delete("/sessions/{harness_type}/{session_id}")
async def delete_session(harness_type: str, session_id: str):
    """Delete a session."""
    if not session_store:
        return JSONResponse(status_code=503, content={"error": "Service not ready"})
    deleted = session_store.delete_session(harness_type, session_id)
    return {"status": "deleted" if deleted else "not_found"}


# ── Event Query (REST) ──────────────────────────────────────────────

@app.get("/sessions/{harness_type}/{session_id}/events")
async def get_session_events(
    harness_type: str,
    session_id: str,
    after_event_id: Optional[str] = Query(None),
    limit: int = Query(200, ge=1, le=1000),
):
    """REST endpoint for historical event replay (断点续传)."""
    if not event_store:
        return JSONResponse(status_code=503, content={"error": "Service not ready"})
    events = event_store.get_events(
        harness_type,
        session_id,
        after_event_id=after_event_id or "",
        limit=limit,
    )
    return {"events": events}


# ── WebSocket Endpoints ──────────────────────────────────────────────

@app.websocket("/ws/ingest")
async def ws_ingest(websocket: WebSocket):
    """Gateway 主动连接推送 turn 事件.

    Query params: harness_type, session_id, harness_id
    """
    await websocket.accept()
    try:
        # Parse registration
        params = dict(websocket.query_params)
        harness_type = params.get("harness_type", "")
        session_id = params.get("session_id", "")
        harness_id = params.get("harness_id", "")

        if not all([harness_type, session_id, harness_id]):
            await websocket.send_json({"error": "Missing required params: harness_type, session_id, harness_id"})
            await websocket.close()
            return

        # Register session
        if session_store:
            session_store.create_session(harness_type, session_id, harness_id)
            session_store.update_last_active(harness_type, session_id)

        logger.info(f"WS ingest connected: {harness_type}/{session_id} (harness_id={harness_id})")

        # Receive loop
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type")

            if msg_type == "event":
                # Parse event
                try:
                    event = ObserveEvent.from_dict(data.get("payload", {}))
                    # Validate matches registered session
                    if event.harness_type != harness_type or event.session_id != session_id:
                        logger.warning(f"Event mismatch: expected {harness_type}/{session_id}, got {event.harness_type}/{event.session_id}")
                        continue
                    # Emit (persist + broadcast)
                    if emitter:
                        await emitter.emit(event)
                        # Update last_active + ADR-1: backfill agent_id from
                        # the event if the session row lacks it (the WS ingest
                        # URL carries no agent_id; it arrives in the payload).
                        if session_store:
                            session_store.update_last_active(harness_type, session_id)
                            if getattr(event, "agent_id", ""):
                                session_store.update_agent_id(
                                    harness_type, session_id, event.agent_id
                                )
                except Exception as e:
                    logger.error(f"Failed to parse event: {e}")

            elif msg_type == "ping":
                await websocket.send_json({"type": "pong"})

    except WebSocketDisconnect:
        logger.info(f"WS ingest disconnected: {harness_type}/{session_id}")
    except Exception as e:
        logger.error(f"WS ingest error: {e}")
        await websocket.close()


@app.websocket("/ws/subscribe")
async def ws_subscribe(websocket: WebSocket):
    """前端订阅实时事件流.

    Query params: harness_type, session_id, after_event_id (optional)
    """
    await websocket.accept()
    try:
        params = dict(websocket.query_params)
        harness_type = params.get("harness_type", "")
        session_id = params.get("session_id", "")
        after_event_id = params.get("after_event_id", "")

        if not all([harness_type, session_id]):
            await websocket.send_json({"error": "Missing params: harness_type, session_id"})
            await websocket.close()
            return

        logger.info(f"WS subscribe: {harness_type}/{session_id}")

        # Subscribe to emitter
        if emitter:
            await emitter.subscribe(harness_type, session_id, websocket)

            # Replay history (catch-up)
            replay_count = await emitter.replay(
                harness_type, session_id, websocket, after_event_id=after_event_id or None
            )
            logger.info(f"Replayed {replay_count} events to {harness_type}/{session_id}")

            # Keep connection alive for real-time events
            while True:
                # Receive client pings
                data = await websocket.receive_json()
                if data.get("type") == "ping":
                    await websocket.send_json({"type": "pong"})

    except WebSocketDisconnect:
        logger.info(f"WS subscribe disconnected: {harness_type}/{session_id}")
        if emitter:
            await emitter.unsubscribe(harness_type, session_id, websocket)
    except Exception as e:
        logger.error(f"WS subscribe error: {e}")
        if emitter:
            await emitter.unsubscribe(harness_type, session_id, websocket)
        await websocket.close()


# ── Main Entry ─────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8002)
