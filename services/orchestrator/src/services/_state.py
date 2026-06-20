"""Shared service singletons — initialized by engine.py during startup."""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from typing import Any


# ── SSE helper ─────────────────────────────────────────────────────

def sse(event: str, data: dict) -> str:
    """Format an SSE message string."""
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


# ── Agent state ────────────────────────────────────────────────────

agents: dict[str, dict[str, Any]] = {}

# ── LLM ────────────────────────────────────────────────────────────

llm_client: Any = None

# ── Memory ─────────────────────────────────────────────────────────

knowledge_graph: Any = None
# NOTE(D-27): vector_store and embedding_provider removed.
# MemoryService uses KG-based structured recall.
memory_service: Any = None
# pg_store removed — was never fully wired; keeping None for graceful if-check compatibility
pg_store: Any = None

# ── Context & compression ──────────────────────────────────────────

context_monitor: Any = None
async_compressor: Any = None
sync_compressor: Any = None
memory_migrator: Any = None
active_forgetting: Any = None

# ── Orchestration ──────────────────────────────────────────────────

context_manager: Any = None
context_compiler: Any = None
tool_executor: Any = None
communication_bus: Any = None
concurrency_controller: Any = None

# ── Memory event bus ──────────────────────────────────────────────

# P1: internal lifecycle event bus (structured events + hooks). Decoupled
# from the SSE push below — DefaultMemoryHook calls emit_memory_event as
# a side-effect of handling an event.
memory_event_bus: Any = None

# P3: deterministic state pruner + task-post consolidator
state_pruner: Any = None
task_consolidator: Any = None

# External-memory watcher: 60s poll for external DB writes + on-demand maintenance.
db_watcher: Any = None
_db_watch_task: Any = None  # asyncio task handle (liveness)

memory_event_subscribers: list[asyncio.Queue[str]] = []


def emit_memory_event(event: str, details: dict[str, Any]) -> None:
    """Broadcast a memory lifecycle event to all SSE subscribers."""
    sse_msg = sse("memory_event", {"event": event, **details})
    dead: list[asyncio.Queue[str]] = []
    for q in memory_event_subscribers:
        try:
            q.put_nowait(sse_msg)
        except asyncio.QueueFull:
            dead.append(q)
    for q in dead:
        memory_event_subscribers.remove(q)


def subscribe_memory_events() -> asyncio.Queue[str]:
    """Register a queue to receive memory lifecycle SSE events."""
    q: asyncio.Queue[str] = asyncio.Queue(maxsize=200)
    memory_event_subscribers.append(q)
    return q


def unsubscribe_memory_events(q: asyncio.Queue[str]) -> None:
    """Remove a previously subscribed event queue."""
    if q in memory_event_subscribers:
        memory_event_subscribers.remove(q)


# ── Execution log ──────────────────────────────────────────────────

execution_log: list[dict[str, Any]] = []
MAX_EXECUTION_LOG: int = 1000


def log_execution_step(node_name: str, state: Any, status: str = "done") -> None:
    """Append a step to the execution log (capped at MAX_EXECUTION_LOG)."""
    entry = {
        "id": str(uuid.uuid4()),
        "node_id": node_name,
        "agent_id": state.agent_id,
        "session_id": state.session_id,
        "status": status,
        "input": state.input[:200],
        "output": (state.output or "")[:200],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    execution_log.append(entry)
    if len(execution_log) > MAX_EXECUTION_LOG:
        del execution_log[: len(execution_log) - MAX_EXECUTION_LOG]
