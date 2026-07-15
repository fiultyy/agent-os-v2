"""Orchestration engine — entry point for the orchestrator service.

Provides:
- FastAPI app with agent CRUD + execution endpoints.
- Graph execution with memory integration and SSE event emission.
- Phase 8: Multi-agent communication, concurrency, shared memory.
- Phase 9: Memory API, Communication API, Debug API, Knowledge Graph API.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# orche app logging:默认无配置 → harness.* 的 INFO/error 全丢(uvicorn 只收 access log)。
# 加 FileHandler 到 src.harness 树,落盘 /tmp/orch-harness.log,便于 debug openclaw client。
_harness_log = logging.getLogger("src.harness")
if not any(isinstance(h, logging.FileHandler) and getattr(h, "_orch_harness", False)
           for h in _harness_log.handlers):
    _fh = logging.FileHandler("/tmp/orch-harness.log")
    _fh._orch_harness = True
    _fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    _harness_log.addHandler(_fh)
_harness_log.setLevel(logging.INFO)
logging.getLogger("src.harness.openclaw").setLevel(logging.INFO)

from fastapi import FastAPI

from src.memory import (
    MemoryService,
    SQLiteStore,
    MemoryEventBus,
    DefaultMemoryHook,
)
from src.memory.knowledge_graph import KnowledgeGraph
from src.memory.compressor import AsyncCompressor, SyncCompressor, ContextMonitor
from src.memory.migrator import MemoryMigrator
from src.memory.forgetting import ActiveForgetting
from src.memory.state_pruner import TimeBasedStatePruner
from src.memory.sideline.task_consolidator import TaskConsolidationAgent
from src.memory.sideline.backward_writer import BackwardWriter
from src.memory.db_watcher import MemoryDBWatcher
from src.memory.write_queue import MemoryWriteQueue
from src.communication.bus import CommunicationBus
from src.concurrency.controller import ConcurrencyController
from src.tools.executor import ToolExecutor
from src.tools.registry import ToolRegistry
from src.context import ContextManager, ContextCompiler

from src.services import _state
from src.services.llm_client import LLMClient
from src.services.agent_manager import init_default_agent, restore_agents_from_pg

# ── FastAPI app ────────────────────────────────────────────────────

app = FastAPI(title="Agent OS — Orchestrator", version="0.2.0", redirect_slashes=False)

# ── Initialize shared state ────────────────────────────────────────

_state.llm_client = LLMClient()

# #4: side-agent 专用 LLM 实例(记忆提炼/整合/curator 等)。SIDE_LLM_ENABLED=1
# 时装配;未启用 → None,side agent fallback 到主 llm_client,灰度安全。
# format-aware:paas/v4(openai 兼容)或 anthropic 通道二选一。
# 注意:智谱 paas/v4 通道(glm-4-flash 等)需单独资源包,余额不足时 429;
# glm-4.7 走 anthropic 通道(/api/anthropic,套餐内可用),所以 memory 端
# 默认切 anthropic + glm-4.7(与主对话 glm-5-turbo 同通道、不同 model)。
# _chat_anthropic 用 self.anthropic_model(忽略 OpenAI default_model),
# 故 anthropic 分支必须配 anthropic_* 字段。
if os.getenv("SIDE_LLM_ENABLED", "0") == "1":
    _side_fmt = os.getenv("SIDE_LLM_API_FORMAT", "anthropic").lower()
    if _side_fmt == "anthropic":
        _state.side_llm_client = LLMClient(
            format="anthropic",
            anthropic_model=os.getenv("SIDE_LLM_MODEL", "glm-4.7"),
            anthropic_base_url=os.getenv(
                "SIDE_LLM_ANTHROPIC_BASE_URL",
                os.environ.get("ANTHROPIC_BASE_URL", "https://open.bigmodel.cn/api/anthropic"),
            ),
            anthropic_api_key=os.getenv(
                "SIDE_LLM_ANTHROPIC_API_KEY",
                os.environ.get("ANTHROPIC_AUTH_TOKEN", ""),
            ),
        )
    else:
        _state.side_llm_client = LLMClient(
            format="openai",
            base_url=os.getenv("SIDE_LLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4"),
            api_key=os.getenv("SIDE_LLM_API_KEY", os.environ.get("ANTHROPIC_AUTH_TOKEN", "")),
            default_model=os.getenv("SIDE_LLM_MODEL", "glm-4-flash"),
        )
    logger.info(
        "side-agent LLM wired: format=%s model=%s",
        _state.side_llm_client.format,
        getattr(_state.side_llm_client, "anthropic_model", None)
        or _state.side_llm_client.default_model,
    )
else:
    _state.side_llm_client = None
# side agent 注入源:启用 side 实例则用它,否则 fallback 主 client(行为等同改动前)。
_side_llm = _state.side_llm_client or _state.llm_client

# Knowledge graph must be created first — MemoryService depends on it.
_state.knowledge_graph = KnowledgeGraph()
# NOTE(D-27): FAISS vector_store removed — MemoryService uses KG-based
# structured recall instead.  FAISSVectorStore class is kept for any
# external references.
_state.memory_service = MemoryService(
    SQLiteStore(), knowledge_graph=_state.knowledge_graph
)

# Context compression components
_state.context_monitor = ContextMonitor()
_state.async_compressor = AsyncCompressor(monitor=_state.context_monitor)
_state.sync_compressor = SyncCompressor(monitor=_state.context_monitor)

# Memory migration and active forgetting
_state.memory_migrator = MemoryMigrator(_state.memory_service)
_state.active_forgetting = ActiveForgetting(_state.memory_service)

_state.context_manager = ContextManager(_state.memory_service)
_state.context_compiler = ContextCompiler(_state.context_manager)

# ── Tool register (L2 通电):清单制注册已实现的 primitive+skill 工具 ─────
# composite(browser_flow_execute / code_review_run)显式跳过 —— 它们重依赖
# browser / playwright / code 编排链,在最小 wiring 下会拖累启动。import 容错:
# skill 系统的可选依赖(如 pyyaml)缺失时降级为空 registry,不阻断 engine 启动。
from src.tools.catalog import ToolLayer

_tool_registry = ToolRegistry()
try:
    from src.skills.primitive import (
        http_get, http_post, http_put, http_delete, http_patch,
        file_read, file_write, file_delete, file_exists, file_list, file_mkdir,
        db_query, db_execute, db_transaction, db_schema,
    )
    from src.skills.code import code_read, code_write, code_search
    _SKILL_TOOLS_AVAILABLE = True
except ImportError as _skill_import_err:
    logger.warning(
        "skill tools unavailable (optional deps missing): %s", _skill_import_err,
    )
    _SKILL_TOOLS_AVAILABLE = False

if _SKILL_TOOLS_AVAILABLE:
    # 清单:(name, handler, description, parameters_schema, layer)。注册数由
    # 清单长度决定 —— 不硬编码(生产应为 15 primitive + 3 skill = 18)。
    _PRIMITIVE_TOOLS: list[tuple] = [
        ("http_get", http_get, "HTTP GET 请求",
         {"type": "object", "properties": {"url": {"type": "string"}, "headers": {"type": "object"}, "timeout": {"type": "integer"}}, "required": ["url"]}, ToolLayer.PRIMITIVE),
        ("http_post", http_post, "HTTP POST 请求",
         {"type": "object", "properties": {"url": {"type": "string"}, "body": {"type": "string"}, "headers": {"type": "object"}, "timeout": {"type": "integer"}}, "required": ["url"]}, ToolLayer.PRIMITIVE),
        ("http_put", http_put, "HTTP PUT 请求",
         {"type": "object", "properties": {"url": {"type": "string"}, "body": {"type": "string"}, "headers": {"type": "object"}, "timeout": {"type": "integer"}}, "required": ["url"]}, ToolLayer.PRIMITIVE),
        ("http_delete", http_delete, "HTTP DELETE 请求",
         {"type": "object", "properties": {"url": {"type": "string"}, "headers": {"type": "object"}, "timeout": {"type": "integer"}}, "required": ["url"]}, ToolLayer.PRIMITIVE),
        ("http_patch", http_patch, "HTTP PATCH 请求",
         {"type": "object", "properties": {"url": {"type": "string"}, "body": {"type": "string"}, "headers": {"type": "object"}, "timeout": {"type": "integer"}}, "required": ["url"]}, ToolLayer.PRIMITIVE),
        ("file_read", file_read, "读取文件内容",
         {"type": "object", "properties": {"path": {"type": "string"}, "encoding": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["path"]}, ToolLayer.PRIMITIVE),
        ("file_write", file_write, "写入文件内容",
         {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}, "encoding": {"type": "string"}, "mode": {"type": "string"}}, "required": ["path", "content"]}, ToolLayer.PRIMITIVE),
        ("file_delete", file_delete, "删除文件或目录",
         {"type": "object", "properties": {"path": {"type": "string"}, "recursive": {"type": "boolean"}}, "required": ["path"]}, ToolLayer.PRIMITIVE),
        ("file_exists", file_exists, "检查路径是否存在",
         {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}, ToolLayer.PRIMITIVE),
        ("file_list", file_list, "列出目录内容",
         {"type": "object", "properties": {"path": {"type": "string"}, "pattern": {"type": "string"}, "recursive": {"type": "boolean"}, "max_depth": {"type": "integer"}}, "required": ["path"]}, ToolLayer.PRIMITIVE),
        ("file_mkdir", file_mkdir, "创建目录",
         {"type": "object", "properties": {"path": {"type": "string"}, "parents": {"type": "boolean"}, "exist_ok": {"type": "boolean"}}, "required": ["path"]}, ToolLayer.PRIMITIVE),
        ("db_query", db_query, "执行 SELECT 查询",
         {"type": "object", "properties": {"sql": {"type": "string"}, "params": {"type": "array"}, "db_path": {"type": "string"}, "fetch": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["sql"]}, ToolLayer.PRIMITIVE),
        ("db_execute", db_execute, "执行 INSERT/UPDATE/DELETE",
         {"type": "object", "properties": {"sql": {"type": "string"}, "params": {"type": "array"}, "db_path": {"type": "string"}, "commit": {"type": "boolean"}}, "required": ["sql"]}, ToolLayer.PRIMITIVE),
        ("db_transaction", db_transaction, "事务执行多条 SQL",
         {"type": "object", "properties": {"statements": {"type": "array"}, "db_path": {"type": "string"}}, "required": ["statements"]}, ToolLayer.PRIMITIVE),
        ("db_schema", db_schema, "获取表结构",
         {"type": "object", "properties": {"table": {"type": "string"}, "db_path": {"type": "string"}}, "required": ["table"]}, ToolLayer.PRIMITIVE),
    ]
    _SKILL_TOOLS: list[tuple] = [
        ("code_read", code_read, "读取代码文件",
         {"type": "object", "properties": {"path": {"type": "string"}, "language": {"type": "string"}, "start_line": {"type": "integer"}, "end_line": {"type": "integer"}, "highlight": {"type": "boolean"}}, "required": ["path"]}, ToolLayer.SKILL),
        ("code_write", code_write, "写入代码文件",
         {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}, "language": {"type": "string"}, "backup": {"type": "boolean"}, "atomic": {"type": "boolean"}, "encoding": {"type": "string"}}, "required": ["path", "content"]}, ToolLayer.SKILL),
        ("code_search", code_search, "搜索代码文件",
         {"type": "object", "properties": {"query": {"type": "string"}, "path": {"type": "string"}, "pattern_type": {"type": "string"}, "file_filter": {"type": "string"}, "case_sensitive": {"type": "boolean"}, "context_lines": {"type": "integer"}, "max_results": {"type": "integer"}, "recursive": {"type": "boolean"}, "max_depth": {"type": "integer"}}, "required": ["query"]}, ToolLayer.SKILL),
    ]
    # composite skipped: browser_flow_execute / code_review_run (heavy deps)

    def _bulk_register(registry, items):
        n = 0
        for name, handler, desc, params, layer in items:
            registry.register(
                name, handler, description=desc, parameters=params, layer=layer,
            )
            n += 1
        return n

    _n_prim = _bulk_register(_tool_registry, _PRIMITIVE_TOOLS)
    _n_skill = _bulk_register(_tool_registry, _SKILL_TOOLS)
    logger.info(
        "tool register: primitive=%d skill=%d total=%d | list_tools=%d catalog.count=%d "
        "(composite skipped: browser_flow/code_review)",
        _n_prim, _n_skill, _n_prim + _n_skill,
        len(_tool_registry.list_tools()), _tool_registry.get_catalog().count(),
    )

_state.tool_executor = ToolExecutor(_tool_registry)
_state.communication_bus = CommunicationBus()

# 通信桥(后端侧):把 CommunicationBus 的 direct-delivery 桥到 SSE 流 ——
# register_delivery_callback 此前全仓零调用。direct agent-to-agent 投递现在会
# 经 _state.emit_agent_message 推一条 agent_message SSE 事件(前端 dispatch 是 L4)。
async def _bridge_agent_delivery(message, recipient_id):
    try:
        _state.emit_agent_message(message, recipient_id)
    except Exception:
        logger.warning("agent_message SSE bridge failed", exc_info=True)


_state.communication_bus.register_delivery_callback(_bridge_agent_delivery)
_state.concurrency_controller = ConcurrencyController()

# ── 持久化双向(原 L5 并入):PostgresStore 做 agent 持久化 ──────────────
# 模块级只建 engine(不连池);``await initialize()`` 在 startup hook 执行,失败
# 降级为 None —— 保留所有 call-site 的 ``is not None`` guard 语义。
_pg_url = os.getenv("DATABASE_URL") or os.getenv("PG_DATABASE_URL") or ""
if _pg_url:
    try:
        from src.memory.pgstore import PostgresStore
        _state.pg_store = PostgresStore(_pg_url)
        logger.info("PostgresStore configured (DATABASE_URL set); initializing on startup")
    except Exception:
        logger.warning("PostgresStore init failed — degrading pg_store to None", exc_info=True)
        _state.pg_store = None
else:
    _state.pg_store = None

# PitFail 通电(异步零依赖):模块级实例化 PitfailRegistry。构造即 _init_db 建
# 表,无需 async initialize(区别于 pg_store 的 startup-hook 模式)。失败降级为
# None —— chat.py 工具失败分支与 /v1/pitfall API 均 guard ``is not None``。
try:
    from src.pitfail import PitfailRegistry
    _state.pitfail_registry = PitfailRegistry(os.getenv("PITFALLS_DB", "data/pitfalls.db"))
    logger.info("PitfailRegistry wired (db=%s)", _state.pitfail_registry.db_path)
except Exception:
    logger.warning("PitfailRegistry init failed — degrading pitfail_registry to None", exc_info=True)
    _state.pitfail_registry = None

# Conversation history(通电):对话历史持久化,参照 pitfail 模式。chat.py /execute
# 完成后 record_turn 落库;/v1/conversations API + call-site 均 is-not-None guard。
try:
    from src.conversation import ConversationRegistry
    _state.conversation_registry = ConversationRegistry(os.getenv("CONVERSATIONS_DB", "data/conversations.db"))
    logger.info("ConversationRegistry wired (db=%s)", _state.conversation_registry.db_path)
except Exception:
    logger.warning("ConversationRegistry init failed — degrading conversation_registry to None", exc_info=True)
    _state.conversation_registry = None

# NOT-WIRED (deferred): ConditionalSpawner 装配块已移除 —— 生产路径
# /v1/orchestrate 经 routes/orchestrate.py:_agent_manager_shim() +
# _build_multi_agent_graph() 直接调 agent_manager 模块函数,完全绕过 spawner,
# 故 .spawn() 全树零生产调用,spawner 实例永远空配置。ConditionalSpawner 类
# 本身保留(defer 代码,未来 CRON/EVENT/QUEUE 自动触发型 subagent 复用,见
# docs/multi-agent-poweron-roadmap.md:8(a))。独立 _orchestration_bus 随装配块
# 一并消失(无其他 reader)。

# P1: memory event bus + default lifecycle hook. chat.py emits lifecycle
# events instead of calling memory_service/memory_migrator directly.
_state.memory_event_bus = MemoryEventBus()

# W3: bounded-concurrency write pool (multi-agent) + per-agent ordering.
# DefaultMemoryHook routes every store/migrate/update through it; chat.py's
# fire-and-forget writes (INGEST/SESSION_END/consolidate) go through fire().
_state.write_queue = MemoryWriteQueue(
    concurrency=int(os.getenv("MEMORY_WRITE_CONCURRENCY", "8")),
    drain_timeout=float(os.getenv("MEMORY_WRITE_DRAIN_TIMEOUT", "5")),
)
_state.memory_event_bus.register(
    DefaultMemoryHook(
        memory_service=_state.memory_service,
        memory_migrator=_state.memory_migrator,
        sync_compressor=_state.sync_compressor,
        async_compressor=_state.async_compressor,
        context_monitor=_state.context_monitor,
        write_queue=_state.write_queue,
    )
)
# Degradation switch: MEMORY_EVENT_BUS_ENABLED=0 keeps only the SYSTEM
# (DefaultMemoryHook) hooks and skips observer hooks — equivalent to
# pre-P1 behaviour. chat.py always goes through bus.emit.
if os.getenv("MEMORY_EVENT_BUS_ENABLED", "1") != "1":
    _state.memory_event_bus.set_enabled(False)

# P3: deterministic state pruner (zero-LLM-cost膨胀控制) + task-post
# consolidator (background_review style online consolidation).
_state.state_pruner = TimeBasedStatePruner(_state.memory_service)
_state.task_consolidator = TaskConsolidationAgent(
    _state.memory_service,
    _side_llm,
    BackwardWriter(_state.memory_service, _side_llm),
)
# External-memory watcher: detects external DB writes (other harnesses sharing
# the sqlite DB) and runs the deterministic maintenance chain. Zero LLM.
_state.db_watcher = MemoryDBWatcher(
    _state.memory_service,
    poll_interval=float(os.getenv("MEMORY_DB_WATCH_INTERVAL", "60")),
)

# ── Memory-kernel side agents (Part 1) + neural field (Part 2) ─────
# Five feature-gated singletons. Each is default-OFF (grey-rollout):
#  MEMORY_INGESTOR_ENABLED / MEMORY_CONSOLIDATOR_ENABLED /
#  MEMORY_RETRIEVER_ENABLED / MEMORY_CURATOR_ENABLED /
#  MEMORY_NEURAL_FIELD_ENABLED. When off the hook is not registered, so the
# bus emit for that event is a no-op and behaviour matches the deterministic
# baseline (zero regression). Review correction #12: each hook is registered
# explicitly for ONE event (register(hook, EventType.X)) — never the default
# all-events mount, which would fan all 10 events to every hook.

from src.memory.event_bus import EventType
from src.memory.sideline.ingestor_agent import IngestorAgent, IngestorHook
from src.memory.sideline.consolidator_agent import ConsolidatorAgent, ConsolidatorHook
from src.memory.sideline.retriever_agent import RetrieverAgent, RetrieverHook
from src.memory.sideline.curator_agent import CuratorAgent, CuratorHook
from src.memory.runtime_observer import RuntimeObserverHook
from src.memory.neural_field import (
    NeuralFieldEngine,
    NeuralFieldStore,
    NeuralFieldRobustness,
    NeuralHook,
)

_neural_store = NeuralFieldStore("data/neural_field.db")
_neural_engine = NeuralFieldEngine()
_neural_robustness = NeuralFieldRobustness(_neural_store, _neural_engine)

if os.getenv("MEMORY_INGESTOR_ENABLED", "0") == "1":
    _state.ingestor = IngestorAgent(
        memory_service=_state.memory_service,
        llm_client=_side_llm,
        kg=_state.knowledge_graph,
    )
    # Review correction #12: explicit single-event registration.
    _state.memory_event_bus.register(
        IngestorHook(_state.ingestor), EventType.INGEST
    )

if os.getenv("MEMORY_CONSOLIDATOR_ENABLED", "0") == "1":
    _state.consolidator = ConsolidatorAgent(
        memory_service=_state.memory_service,
        llm_client=_side_llm,
    )
    # Consolidator fires on both CONSOLIDATE (periodic) and SESSION_END.
    _state.memory_event_bus.register(
        ConsolidatorHook(_state.consolidator),
        EventType.CONSOLIDATE,
        EventType.SESSION_END,
    )

if os.getenv("MEMORY_RETRIEVER_ENABLED", "0") == "1":
    _state.retriever = RetrieverAgent(
        memory_service=_state.memory_service,
        kg=_state.knowledge_graph,
    )
    _state.memory_event_bus.register(
        RetrieverHook(_state.retriever), EventType.RECALL
    )

if os.getenv("MEMORY_CURATOR_ENABLED", "0") == "1":
    _state.curator = CuratorAgent(
        memory_service=_state.memory_service,
        llm_client=_side_llm,
    )
    _state.memory_event_bus.register(
        CuratorHook(_state.curator), EventType.CURATE
    )

if os.getenv("MEMORY_NEURAL_FIELD_ENABLED", "0") == "1":
    _state.neural_store = _neural_store
    _state.neural_engine = _neural_engine
    _state.neural_hook = NeuralHook(
        engine=_neural_engine,
        store=_neural_store,
        robustness=_neural_robustness,
        kg=_state.knowledge_graph,
    )
    _state.memory_event_bus.register(
        # P0-1 扩展:TURN_END(chat/execute turn)+ INGEST(store_memory/sync_extract
        # 写入)。后者让 openclaw 沉积路径(不经 chat)也喂 neural drift。NeuralHook
        # OBSERVER 返回 None,emit 取 last non-None,不覆盖 IngestorHook 的 IngestorResult。
        _state.neural_hook, EventType.TURN_END, EventType.INGEST,
    )

# Runtime observer hook — introspective observability (error-spike detection).
# Pure read of _state.execution_log → runtime_observations ring buffer; zero
# LLM, zero memory writes. Unconditional (non-fatal; auto-skipped under
# MEMORY_EVENT_BUS_ENABLED=0 degradation). OBSERVER priority, returns None.
_state.memory_event_bus.register(
    RuntimeObserverHook(), EventType.TURN_END, EventType.SESSION_END,
)

# Ensure data directory exists for SQLite databases
Path("data").mkdir(exist_ok=True)

# ── Lifecycle hooks ────────────────────────────────────────────────
# NOTE: FastAPI runs startup hooks in registration order. The PG hook below
# is registered BEFORE _init_default_agent_hook on purpose: it initializes the
# store and restores persisted agents first, so init_default_agent sees a
# non-empty _state.agents and skips re-creating the default. If PG is
# unavailable (pg_store is None) the hook is a no-op and behaviour matches the
# in-memory baseline.


@app.on_event("startup")
async def _init_pg_store_and_restore() -> None:
    """Initialize PostgresStore and restore persisted agents (read side)."""
    if _state.pg_store is None:
        return
    try:
        await _state.pg_store.initialize()
    except Exception:
        logger.warning(
            "pg_store.initialize failed — degrading pg_store to None", exc_info=True,
        )
        _state.pg_store = None
        return
    restored = await restore_agents_from_pg()
    logger.info("pg_store initialized; restored %d agent(s) from PG", restored)


@app.on_event("startup")
async def _init_default_agent_hook() -> None:
    """Auto-create a default agent if none exist."""
    await init_default_agent()


@app.on_event("startup")
async def _start_forgetting_sweep() -> None:
    """Run forgetting sweep periodically (daily)."""

    async def _sweep_loop():
        while True:
            await asyncio.sleep(86400)  # 24 hours
            try:
                for agent_id in list(_state.agents.keys()):
                    # P3: deterministic state pruning first (zero-LLM-cost
                    #膨胀控制), then importance-based forgetting, then migration.
                    if _state.state_pruner is not None:
                        prune_result = await _state.state_pruner.prune(agent_id=agent_id)
                        _state.emit_memory_event("prune", {
                            "agent_id": agent_id,
                            "scanned": prune_result.scanned,
                            "stale": prune_result.to_stale,
                            "archived": prune_result.to_archived,
                        })
                    forget_result = await _state.active_forgetting.run_sweep(agent_id=agent_id)
                    _state.emit_memory_event("forget", {
                        "agent_id": agent_id,
                        "scanned": forget_result.scanned,
                        "archived": forget_result.archived,
                        "archived_ids": forget_result.archived_ids[:10],
                    })
                    migrate_ids = await _state.memory_migrator.migrate_episodic_to_semantic(agent_id)
                    _state.emit_memory_event("migrate", {
                        "agent_id": agent_id,
                        "path": "episodic_to_semantic",
                        "count": len(migrate_ids),
                        "ids": migrate_ids[:10],
                    })
            except Exception:
                pass

    asyncio.create_task(_sweep_loop())


@app.on_event("startup")
async def _start_db_watch() -> None:
    """Poll for external memory DB writes and run deterministic maintenance."""

    if _state.db_watcher is None:
        logger.error("db_watcher not initialized — skipping db_watch_loop")
        return
    interval = _state.db_watcher.poll_interval

    async def _watch_loop():
        while True:
            await asyncio.sleep(interval)
            try:
                # Offload the synchronous MAX(updated_at) query off the event loop.
                changed = await asyncio.to_thread(_state.db_watcher.has_external_changes)
                # (1) deterministic chain (zero-LLM) runs ONLY when the DB
                # advanced past the watermark (an external write was detected).
                # A fresh write RESETS the write-idle clock so a burst of
                # writes defers the LLM tiers (2)/(3) to the NEXT quiet window.
                if changed is not None:
                    _state.db_watcher.bump_write_clock(changed)
                    await _state.db_watcher.run_once_all(emit=True, trigger="poll")
                # (2)/(3) idle tiers (extract / consolidate) run REGARDLESS of
                # `changed` — this is the quiet-window digest path. They self-
                # gate on write-idle + cadence + pending backlog, and an
                # in-flight write (handled above) bumps last_write_ts so the
                # internal idle gate defers them during a write burst. Running
                # them unconditionally here is what lets a quiet window after
                # the last change actually digest the backlog.
                await _state.db_watcher.run_idle_once_all(trigger="idle_poll")
            except Exception:
                logger.exception("db_watch_loop iteration failed")

    _state._db_watch_task = asyncio.create_task(_watch_loop())


@app.on_event("shutdown")
async def _shutdown() -> None:
    """Graceful shutdown: drain in-flight memory writes, then close."""
    # W3: stop accepting new pooled writes, then wait for in-flight ones so
    # nothing is lost on shutdown (drain is bounded by drain_timeout).
    if _state.write_queue is not None:
        _state.write_queue.shutdown()
        await _state.write_queue.drain()
    await _state.communication_bus.close()


# ── Register route routers ─────────────────────────────────────────

from src.api.routes.agents import router as agents_router
from src.api.routes.memory import router as memory_router
from src.api.routes.chat import router as chat_router, root_router_health
from src.api.routes.entities import router as entities_router
from src.api.routes.pitfail import router as pitfail_router
from src.api.routes.orchestrate import router as orchestrate_router
from src.api.routes.conversations import router as conversations_router
from src.api.routes.canvas import router as canvas_router, init_canvas_routes
from src.canvas.event_store import CanvasEventStore
from src.canvas.emitter import SessionEventEmitter
from src.canvas.tab_manager import TabManager

# Health endpoint stays at root (no version prefix)
app.include_router(root_router_health)

# Canvas routes (WebSocket + REST, /api/canvas prefix)
# Wire up canvas dependencies at import time
# db_path 显式传 volume 内路径:event_store._default_db_path() 用 parent.parent.parent.parent
# 在容器内(/app/src/canvas → 4 层 = /)算到 /data(非 volume),host 看不到。显式传
# data/canvas_events.db(相对 /app WORKDIR → /app/data = host volume),replay 历史才持久。
_canvas_store = CanvasEventStore(os.getenv("CANVAS_EVENTS_DB", "data/canvas_events.db"))
_canvas_tab_mgr = TabManager()
_canvas_emitter = SessionEventEmitter(event_store=_canvas_store)
init_canvas_routes(store=_canvas_store, emitter=_canvas_emitter, tab_manager=_canvas_tab_mgr)
# 暴露 emitter 到 _state:chat.py /execute + _node_tool 经此 emit tick/tool 事件 → 实时画布。
_state.canvas_emitter = _canvas_emitter
app.include_router(canvas_router)

# ── Observe client (T4 multi-harness-observe) ─────────────────────
# 初始化 ObserveClient 并暴露到 _state。chat.py /execute + _node_tool 经此推泛化
# turn 事件 → observe-service WS ingest。None-guard(observe-service 不可达时
# client 内部静默 logger.warning,不 raise)。
try:
    from src.observe.client import ObserveClient
    _state.observe_client = ObserveClient(harness_id="orchestrator-main")
except Exception as e:
    logger.warning("observe client initialization failed: %s", e)
    _state.observe_client = None

# All API routes under /v1 prefix
app.include_router(agents_router, prefix="/v1")
app.include_router(memory_router, prefix="/v1")
app.include_router(chat_router, prefix="/v1")
app.include_router(entities_router, prefix="/v1")
app.include_router(pitfail_router, prefix="/v1")
app.include_router(orchestrate_router, prefix="/v1")
app.include_router(conversations_router, prefix="/v1")

# ── Harness primitive API (ADR-4: orchestrator is the ONLY harness client) ──
# Thin layer over {claw, claude-code}: sessions CRUD + turn + spawn + switch.
# No /v1 prefix — primitive endpoints live at root (/h/..., /switch).
# Event flow: harness → orchestrator (connect+map) → observe /ws/ingest.
from src.harness import router as harness_router, switch_router as harness_switch_router
app.include_router(harness_router)
app.include_router(harness_switch_router)
logger.info("harness primitive API mounted: /h/{type}/sessions*, /switch")


# ── CLI entry point ────────────────────────────────────────────────


def main() -> None:
    """CLI entry point: run the minimal graph demo."""

    async def run_demo():
        from src.api.routes.chat import _build_execution_graph
        from src.graph import GraphState

        graph = _build_execution_graph()

        for i, user_input in enumerate([
            "Hello, Agent OS!",
            "What is the weather today?",
            "Remember my preferences",
        ], 1):
            state = GraphState(input=user_input, agent_id="cli", session_id="cli-session")
            result = await graph.run(state)
            print(f"\n--- Round {i} ---")
            print(f"  Input:  {user_input}")
            print(f"  Output: {result.output}")

        memories = await _state.memory_service.recall(
            query="", agent_id="cli", session_id="cli-session", top_k=10,
        )
        print(f"\n=== {len(memories)} memories stored ===")
        print(f"=== KG stats: {_state.knowledge_graph.stats()} ===")

    asyncio.run(run_demo())


if __name__ == "__main__":
    main()
