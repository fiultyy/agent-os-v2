"""Conversation Observer — FastAPI entry point.

Endpoints:
- Conversation CRUD with turn tracking
- Real-time context retrieval
- Anomaly detection (error spikes, stalled conversations)
- Usage analytics
"""

import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel

from src.monitor import ConversationMonitor
from src.analytics import ConversationAnalytics

app = FastAPI(title="Agent OS — Conversation Observer", version="0.1.0", redirect_slashes=False)

_monitor = ConversationMonitor()
_analytics = ConversationAnalytics()


# ── Models ───────────────────────────────────────────────────────


class CreateConversationRequest(BaseModel):
    agent_id: str = ""
    metadata: dict[str, Any] = {}


class AddTurnRequest(BaseModel):
    role: str
    content: str
    metadata: dict[str, Any] = {}


# ── Health ───────────────────────────────────────────────────────


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


# ── Conversation CRUD ────────────────────────────────────────────


@app.post("/conversations")
async def create_conversation(req: CreateConversationRequest) -> dict:
    conv_id = str(uuid.uuid4())
    conv = _monitor.create_conversation(
        conversation_id=conv_id,
        agent_id=req.agent_id,
        metadata=req.metadata,
    )
    _analytics.record_event("conversation_created", {"conversation_id": conv_id})
    return conv


@app.get("/conversations")
async def list_conversations(agent_id: str = "") -> list[dict]:
    return _monitor.list_conversations(agent_id=agent_id)


@app.get("/conversations/{conversation_id}")
async def get_conversation(conversation_id: str) -> dict:
    conv = _monitor.get_conversation(conversation_id)
    if not conv:
        return {"error": "Conversation not found"}
    return conv


@app.delete("/conversations/{conversation_id}")
async def delete_conversation(conversation_id: str) -> dict:
    deleted = _monitor.delete_conversation(conversation_id)
    if deleted:
        _analytics.record_event("conversation_deleted", {"conversation_id": conversation_id})
        return {"deleted": True}
    return {"error": "Conversation not found"}


# ── Turn management ──────────────────────────────────────────────


@app.post("/conversations/{conversation_id}/turns")
async def add_turn(conversation_id: str, req: AddTurnRequest) -> dict:
    turn = _monitor.add_turn(
        conversation_id=conversation_id,
        role=req.role,
        content=req.content,
        metadata=req.metadata,
    )
    if turn is None:
        return {"error": "Conversation not found"}

    await _monitor.on_message(conversation_id, turn)

    # Anomaly detection: flag conversations with excessive errors
    raw_conv = _monitor.get_raw_conversation(conversation_id)
    if raw_conv:
        error_count = sum(1 for t in raw_conv.get("turns", []) if t.get("role") == "error")
        if error_count > 3:
            raw_conv["status"] = "degraded"
            _analytics.record_event("anomaly_detected", {
                "conversation_id": conversation_id,
                "type": "error_spike",
                "error_count": error_count,
            })

    _analytics.record_event("turn_added", {"conversation_id": conversation_id, "role": req.role})
    return turn


@app.get("/conversations/{conversation_id}/turns")
async def get_turns(conversation_id: str) -> list[dict]:
    return await _monitor.get_turns(conversation_id)


# ── Context tracking ─────────────────────────────────────────────


@app.get("/conversations/{conversation_id}/context")
async def get_context(conversation_id: str) -> dict:
    return await _monitor.get_context(conversation_id)


# ── Analytics ────────────────────────────────────────────────────


@app.get("/analytics/stats")
async def get_stats(time_range: str = "7d") -> dict:
    return await _analytics.get_stats(time_range)
