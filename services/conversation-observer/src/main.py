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
    conv = {
        "id": conv_id,
        "agent_id": req.agent_id,
        "turns": [],
        "metadata": req.metadata,
        "status": "active",
        "context_summary": "",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    _monitor._conversations[conv_id] = conv
    _analytics.record_event("conversation_created", {"conversation_id": conv_id})
    return conv


@app.get("/conversations")
async def list_conversations(agent_id: str = "") -> list[dict]:
    convs = list(_monitor._conversations.values())
    if agent_id:
        convs = [c for c in convs if c["agent_id"] == agent_id]
    return convs


@app.get("/conversations/{conversation_id}")
async def get_conversation(conversation_id: str) -> dict:
    conv = _monitor._conversations.get(conversation_id)
    if not conv:
        return {"error": "Conversation not found"}
    return conv


@app.delete("/conversations/{conversation_id}")
async def delete_conversation(conversation_id: str) -> dict:
    if conversation_id in _monitor._conversations:
        del _monitor._conversations[conversation_id]
        _analytics.record_event("conversation_deleted", {"conversation_id": conversation_id})
        return {"deleted": True}
    return {"error": "Conversation not found"}


# ── Turn management ──────────────────────────────────────────────


@app.post("/conversations/{conversation_id}/turns")
async def add_turn(conversation_id: str, req: AddTurnRequest) -> dict:
    conv = _monitor._conversations.get(conversation_id)
    if not conv:
        return {"error": "Conversation not found"}

    turn = {
        "id": str(uuid.uuid4()),
        "role": req.role,
        "content": req.content,
        "metadata": req.metadata,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    conv["turns"].append(turn)
    conv["updated_at"] = datetime.now(timezone.utc).isoformat()

    await _monitor.on_message(conversation_id, turn)

    # Anomaly detection: flag conversations with excessive errors
    error_count = sum(1 for t in conv["turns"] if t["role"] == "error")
    if error_count > 3:
        conv["status"] = "degraded"
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
