"""Shared service singletons — initialized by engine.py during startup."""

from __future__ import annotations

import logging
import os
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


# ── Agent state ────────────────────────────────────────────────────

agents: dict[str, dict[str, Any]] = {}

# ── LLM ────────────────────────────────────────────────────────────

llm_client: Any = None
# side-agent LLM 应用层 timeout(秒)— 仅 memory/sideline/ 归档模块引用
# (side-agent 并行机制已从系统装配移除,见 ARCHIVED;待 AO2 capability 重接)。
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

# ── Conversation history (通电) ────────────────────────────────────
# ConversationRegistry: SQLite-backed 对话历史。engine.py 模块级实例化(构造即
# _init_db 建表,无需 async initialize),失败降级为 None。chat.py /execute 完成
# 后 record_turn 落库;/v1/conversations API + call-site 均 ``is not None`` guard。
conversation_registry: Any = None

# ── Observe client (T4 multi-harness-observe) ─────────────────────
# ObserveClient 实例(engine.py 装配)。chat.py /execute + _node_tool 经此推泛化
# turn 事件 → observe-service WS ingest。None-guard(observe-service 不可达不崩
# 主路径,ADR-7 零回归红线)。
observe_client: Any = None

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

# ── Profile (ADR-1) + AgentRegistry (P1 决策 3) ──────────────────────
# AgentRegistry:engine.py 启动 load agents.yaml(缺失/坏降级单 native)。下游
# (routes/T6+)经此取 per-agent spec + resolve_workspace/resolve_cwd_scope。
# ProfileRegistry:engine.py 启动 load_all(registry) per-agent 加载各 workspace
# 身份文件。routes._build_native_session 经 make_profile_capabilities 注入 native
# Agent 为 LayerCapability。None = 未装配(启动失败降级),make_profile_capabilities
# (None) 返 [](None-safe)。
agent_registry: Any = None
profile_registry: Any = None

# NOT-WIRED (deferred): ConditionalSpawner 实例装配块已从 engine.py 移除 ——
# 生产 /v1/orchestrate 经 _agent_manager_shim 绕过 spawner,spawn() 零生产调用。
# ConditionalSpawner 类本身保留(见 src/agent/meta/conditional_spawner.py,
# 未来 CRON/EVENT 触发复用)。槽位移除以消除休眠孤岛。

# ── Memory event bus ──────────────────────────────────────────────

# P1: internal lifecycle event bus (structured events + hooks). DefaultMemoryHook
# (SYSTEM) owns memory side-effects; MemoryObserveHook (OBSERVER) forwards
# lifecycle events to observe-service (Part2).
memory_event_bus: Any = None


async def fire(event: Any, ctx: Any) -> Any:
    """Best-effort emit on memory_event_bus. Returns the hook result (last
    non-None from ``bus.emit``), or None if bus is unset / emit errored.

    Callers needing scored/ranked output read the return value (e.g.
    ``GET /v1/memories`` RECALL → RetrieverHook ranked, ``POST /v1/memories``
    INGEST → IngestorResult). Fire-and-forget callers ignore it (no behavior
    change). Swallows emit errors so a bus/hook failure never breaks the
    caller's main flow.

    ponytail: pre-fix (2c560c5) fire was ``-> None`` and discarded emit's
    return → RetrieverHook scored path unreachable via /v1/memories (always
    fell back to service.recall). Returning emit's result closes that gap."""
    # ponytail: None-guard + swallow — test env has bus=None, prod has it wired;
    # emit failures must not drag down turn/tool main flow.
    bus = memory_event_bus
    if bus is None:
        return None
    try:
        return await bus.emit(event, ctx)
    except Exception:
        logger.warning("memory_event_bus.emit(%s) failed (non-fatal)", event, exc_info=True)
        return None

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
# Side-agent 专用 LLM 实例(SIDE_LLM_ENABLED gate)。None → side agent fallback
# 主 llm_client(engine.py: _side_llm = side_llm_client or llm_client)。
side_llm_client: Any = None
neural_store: Any = None
neural_engine: Any = None
neural_hook: Any = None

# Part2: memory lifecycle → observe. ObserveEmitter wired by engine.py;
# engine.py + memory/db_watcher.py ship prune/forget/migrate events (not bus
# EventTypes) directly through it. None until engine.py wires it.
memory_observe_emitter: Any = None


# ── Degradation accounting (#3) ────────────────────────────────────
# Per-agent degrade counters, incremented by each side agent's _degrade().
# Exposed via GET /debug/status — degrade is otherwise silent (logger.warning only).
degraded_stats: dict[str, int] = defaultdict(int)


def record_degrade(agent: str) -> None:
    """Increment the per-agent degrade counter (called from each _degrade())."""
    degraded_stats[agent] += 1


# ── ADR-C1: reset() for test isolation ─────────────────────────────
# ``reset()`` clears all assembled singletons + runtime accumulators back to
# their module-load defaults. Called from ``tests/conftest.py`` autouse fixture
# so each test starts from a clean ``_state`` regardless of what earlier tests
# (or an ``import engine`` in the same process) wired into it. Production never
# calls reset() — ``bootstrap()`` runs once on FastAPI startup.
#
# Two field groups:
#   1. Assembled singletons (set by engine.bootstrap()): all the ``Any = None``
#      fields above. Reset → None.
#   2. Runtime accumulators (agents dict, execution_log, runtime_observations,
#      degraded_stats): populated at runtime. Reset → empty.
# This dual reset closes both pollution vectors a full-suite run hits: leaked
# singletons (db_watcher, memory_observe_emitter background tasks) AND leaked
# runtime state (agents dict carrying a previous test's sessions).

# Names of assembled-singleton fields (declared ``Any = None`` above) that
# engine.bootstrap() populates. Kept as an explicit list (not introspected) so
# adding a new singleton forces a conscious reset() update — silent drift here
# would quietly break test isolation.
_ASSEMBLED_SINGLETON_FIELDS: tuple[str, ...] = (
    "llm_client",
    "side_llm_client",
    "knowledge_graph",
    "memory_service",
    "pg_store",
    "pitfail_registry",
    "conversation_registry",
    "observe_client",
    "context_monitor",
    "async_compressor",
    "sync_compressor",
    "memory_migrator",
    "active_forgetting",
    "context_manager",
    "context_compiler",
    "tool_executor",
    "communication_bus",
    "concurrency_controller",
    "agent_registry",
    "profile_registry",
    "memory_event_bus",
    "write_queue",
    "state_pruner",
    "task_consolidator",
    "db_watcher",
    "_db_watch_task",
    "ingestor",
    "consolidator",
    "retriever",
    "curator",
    "neural_store",
    "neural_engine",
    "neural_hook",
    "memory_observe_emitter",
)


def reset() -> None:
    """Reset ``_state`` to module-load defaults (test isolation, ADR-C1).

    Clears assembled singletons (→ None) + runtime accumulators
    (agents/execution_log/runtime_observations/degraded_stats → empty). Production
    never calls this; ``engine.bootstrap()`` runs once on startup instead. Tests
    call it via the autouse fixture in ``tests/conftest.py`` so a leaked singleton
    or runtime buffer from an earlier test cannot pollute a later one.

    NOTE: this only clears Python references. Background asyncio tasks
    (db_watcher poll loop, memory_observe_emitter WS reconnect, write_queue
    drain) created by a previous bootstrap() hold their own references and may
    keep running in the event loop they were scheduled on. For full hermetic
    isolation each test should run in a fresh process (pytest default) — reset()
    covers the cross-test in-process pollution that doesn't involve those tasks.
    """
    import sys
    _self = sys.modules[__name__]  # set attrs on this module object
    for _name in _ASSEMBLED_SINGLETON_FIELDS:
        setattr(_self, _name, None)
    _self.agents = {}
    _self.execution_log = []
    _self.runtime_observations = []
    _self.degraded_stats = defaultdict(int)


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


# ── Runtime observation (introspective observability) ──────────────
# In-memory ring buffer of runtime observations (anomaly / error-spike /
# stalled). PURE MEMORY — never persisted to memory_service / memories.db /
# the recall path (memory red-line R1-R8). Surfaced via /debug/status
# (recent_observations). The Part2 observe migration removed the legacy
# ``runtime_observation`` SSE push (it shared the now-deleted memory SSE
# stream); runtime observations now live only in this ring buffer.

runtime_observations: list[dict[str, Any]] = []
MAX_RUNTIME_OBSERVATIONS: int = 200


def record_observation(
    kind: str, agent_id: str, detail: dict[str, Any] | None = None,
) -> None:
    """Append a runtime observation to the in-memory ring buffer.

    The buffer feeds ``GET /debug/status``. Pure in-memory — must never write
    to memory_service / memories.
    """
    entry: dict[str, Any] = {
        "kind": kind,
        "agent_id": agent_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if detail:
        entry.update(detail)
    runtime_observations.append(entry)
    if len(runtime_observations) > MAX_RUNTIME_OBSERVATIONS:
        del runtime_observations[: len(runtime_observations) - MAX_RUNTIME_OBSERVATIONS]


def recent_observations(limit: int = 50) -> list[dict[str, Any]]:
    """Read-only snapshot of recent runtime observations (newest first)."""
    return list(reversed(runtime_observations[-limit:]))
