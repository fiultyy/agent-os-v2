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
from typing import Any

logger = logging.getLogger(__name__)

from fastapi import FastAPI

from src.memory import MemoryService, InMemoryStore, SQLiteStore
from src.memory.knowledge_graph import KnowledgeGraph
from src.memory.vector import FAISSVectorStore
from src.memory.embedding import SentenceTransformerProvider
from src.memory.compressor import AsyncCompressor, SyncCompressor, ContextMonitor
from src.memory.migrator import MemoryMigrator
from src.memory.forgetting import ActiveForgetting
from src.communication.bus import CommunicationBus
from src.concurrency.controller import ConcurrencyController
from src.tools.executor import ToolExecutor
from src.tools.registry import ToolRegistry
from src.context import ContextManager, ContextCompiler

from src.services import _state
from src.services.llm_client import LLMClient
from src.services.agent_manager import init_default_agent

# ── FastAPI app ────────────────────────────────────────────────────

app = FastAPI(title="Agent OS — Orchestrator", version="0.2.0", redirect_slashes=False)

# ── Initialize shared state ────────────────────────────────────────

_state.llm_client = LLMClient()

# Knowledge graph must be created first — MemoryService depends on it.
_state.knowledge_graph = KnowledgeGraph()
_state.embedding_provider = SentenceTransformerProvider()
_state.vector_store = FAISSVectorStore(provider=_state.embedding_provider)
_state.memory_service = MemoryService(
    SQLiteStore(), vector_store=_state.vector_store, knowledge_graph=_state.knowledge_graph
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
_state.tool_executor = ToolExecutor(ToolRegistry())
_state.communication_bus = CommunicationBus()
_state.concurrency_controller = ConcurrencyController()

# Ensure data directory exists for SQLite databases
Path("data").mkdir(exist_ok=True)

# ── Postgres store (optional) ──────────────────────────────────────

_database_url = os.environ.get("DATABASE_URL", "")
if _database_url:
    try:
        from src.memory.pgstore import PostgresStore
        _state.pg_store = PostgresStore(_database_url)

        @app.on_event("startup")
        async def _init_pg_store() -> None:
            await _state.pg_store.initialize()
            for agent in await _state.pg_store.list_agents():
                _state.agents[agent["id"]] = agent

    except Exception:
        _state.pg_store = None


# ── Lifecycle hooks ────────────────────────────────────────────────


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


@app.on_event("shutdown")
async def _shutdown() -> None:
    """Graceful shutdown: persist FAISS index, close database connections."""
    _state.vector_store.save()
    if _state.pg_store is not None:
        await _state.pg_store.close()
    await _state.communication_bus.close()


# ── Register route routers ─────────────────────────────────────────

from src.api.routes.agents import router as agents_router
from src.api.routes.memory import router as memory_router
from src.api.routes.chat import router as chat_router, root_router_health
from src.api.routes.entities import router as entities_router

# Health endpoint stays at root (no version prefix)
app.include_router(root_router_health)

# All API routes under /v1 prefix
app.include_router(agents_router, prefix="/v1")
app.include_router(memory_router, prefix="/v1")
app.include_router(chat_router, prefix="/v1")
app.include_router(entities_router, prefix="/v1")


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
