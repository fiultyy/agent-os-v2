"""Shared service singletons — initialized by engine.py during startup."""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from collections import defaultdict
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
# #4: side-agent 专用 LLM 实例(OpenAI 通道 / glm-4-flash)。None = 未启用,
# side agent fallback 到 llm_client(灰度安全)。engine.py 按 SIDE_LLM_ENABLED 装配。
side_llm_client: Any = None
# #1: side agent LLM 应用层 timeout(秒),统一一个值避免 env 爆炸。
# asyncio.wait_for 包裹 LLM 提炼,超时优雅降级;httpx HTTP 层 60s 兜底。
SIDELLM_TIMEOUT: float = float(os.getenv("MEMORY_SIDELLM_TIMEOUT", "40"))

# ── Memory ─────────────────────────────────────────────────────────

knowledge_graph: Any = None
# NOTE(D-27): vector_store and embedding_provider removed.
# MemoryService uses KG-based structured recall.
memory_service: Any = None
# pg_store: PostgreSQL-backed agent persistence (PostgresStore). Wired by
# engine.py at startup when DATABASE_URL is set — the engine is built at import
# time and ``await initialize()`` runs on the startup hook (failure degrades to
# None). Every call-site guards with ``is not None`` and falls back to the
# in-memory ``agents`` dict, so None stays a safe default.
pg_store: Any = None

# ── PitFail (通电) ──────────────────────────────────────────────────
# PitfailRegistry: SQLite-backed踩坑记录库。engine.py 模块级实例化(构造即
# _init_db 建表,无需 async initialize),失败降级为 None。call-site(chat.py
# _node_tool 工具失败分支 + /v1/pitfail API)均以 ``is not None`` guard,所以
# None 是安全默认(零回归 —— 与 pg_store 同模式)。
pitfail_registry: Any = None

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

# W3: bounded-concurrency memory write pool (multi-agent + per-agent
# ordering + drain). DefaultMemoryHook submits writes through it; chat.py
# fire-and-forget writes go through fire(). None until engine.py wires it.
write_queue: Any = None

# P3: deterministic state pruner + task-post consolidator
state_pruner: Any = None
task_consolidator: Any = None

# External-memory watcher: 60s poll for external DB writes + on-demand maintenance.
db_watcher: Any = None
_db_watch_task: Any = None  # asyncio task handle (liveness)

# ── Memory-kernel side agents (Part 1) + neural field (Part 2) ─────
# Feature-gated singletons wired by engine.py. Each is None when its
# MEMORY_*_ENABLED env var is "0" (default, grey-rollout). Route layers
# guard with ``is not None`` before touching these; a None agent means the
# feature is off → the deterministic baseline path runs.
ingestor: Any = None
consolidator: Any = None
retriever: Any = None
curator: Any = None
neural_store: Any = None
neural_engine: Any = None
neural_hook: Any = None

memory_event_subscribers: list[asyncio.Queue[str]] = []


def _push_sse(sse_msg: str) -> None:
    """Push a formatted SSE string to every subscriber queue (pruning full ones)."""
    dead: list[asyncio.Queue[str]] = []
    for q in memory_event_subscribers:
        try:
            q.put_nowait(sse_msg)
        except asyncio.QueueFull:
            dead.append(q)
    for q in dead:
        memory_event_subscribers.remove(q)


def emit_memory_event(event: str, details: dict[str, Any]) -> None:
    """Broadcast a memory lifecycle event to all SSE subscribers."""
    _push_sse(sse("memory_event", {"event": event, **details}))


def emit_agent_message(message: Any, recipient_id: str) -> None:
    """Bridge an inter-agent AgentMessage onto the SSE stream.

    Registered as a CommunicationBus delivery callback by engine.py so direct
    agent-to-agent deliveries surface as ``agent_message`` SSE events over the
    same subscriber stream as memory events. The front-end dispatch of these
    (rendering an agent_message in the UI) is L4; this is the back-end bridge.
    """
    _push_sse(sse("agent_message", {
        "message_id": getattr(message, "id", ""),
        "sender_id": getattr(message, "sender_id", ""),
        "recipient_id": recipient_id,
        "session_id": getattr(message, "session_id", ""),
        "workspace_id": getattr(message, "workspace_id", ""),
        "content": getattr(message, "content", ""),
        "message_type": str(getattr(message, "message_type", "")),
    }))


def subscribe_memory_events() -> asyncio.Queue[str]:
    """Register a queue to receive memory lifecycle SSE events."""
    q: asyncio.Queue[str] = asyncio.Queue(maxsize=200)
    memory_event_subscribers.append(q)
    return q


def unsubscribe_memory_events(q: asyncio.Queue[str]) -> None:
    """Remove a previously subscribed event queue."""
    if q in memory_event_subscribers:
        memory_event_subscribers.remove(q)


# ── Degradation accounting (#3) ────────────────────────────────────
# Per-agent degrade counters, incremented by each side agent's _degrade().
# Exposed via GET /debug/status — degrade is otherwise silent (logger.warning only).
DEGRADED_AGENTS: tuple[str, ...] = (
    "ingestor", "consolidator", "curator",
    "task_consolidator", "backward_writer",
)
degraded_stats: dict[str, int] = defaultdict(int)


def record_degrade(agent: str) -> None:
    """Increment the per-agent degrade counter (called from each _degrade())."""
    degraded_stats[agent] += 1


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
