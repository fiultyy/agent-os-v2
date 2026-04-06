"""Orchestration engine — entry point for the orchestrator service.

Provides:
- FastAPI app with agent CRUD + execution endpoints.
- Graph execution with memory integration and SSE event emission.
- Phase 8: Multi-agent communication, concurrency, shared memory.
- Phase 9: Memory API, Communication API, Debug API, Knowledge Graph API.
"""

import asyncio
import json
import logging
import os
import re as _re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncGenerator

logger = logging.getLogger(__name__)

import httpx
from fastapi import FastAPI, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from src.graph import StateGraph, GraphState, InMemoryCheckpointStore
from src.graph.nodes import FunctionNode
from src.memory import MemoryService, InMemoryStore, SQLiteStore, MemoryType, MemoryScope
from src.memory.permissions import PermissionManager, PermissionLevel
from src.memory.knowledge_graph import KnowledgeGraph
from src.memory.vector import FAISSVectorStore
from src.memory.embedding import SentenceTransformerProvider
from src.memory.compressor import (
    AsyncCompressor, SyncCompressor, ContextMonitor, CompressionLevel,
)
from src.memory.migrator import MemoryMigrator
from src.memory.forgetting import ActiveForgetting
from src.communication.bus import CommunicationBus
from src.communication.message import AgentMessage, MessageType, MessagePriority
from src.communication.scope import ScopeManager, ScopeLevel
from src.concurrency.controller import ConcurrencyController
from src.tools.executor import ToolExecutor
from src.tools.registry import ToolRegistry
from src.tools.guardrail import Guardrail
from src.context import ContextManager, ContextCompiler


# ── LLM Client ────────────────────────────────────────────────────


class LLMError(Exception):
    """Raised when the LLM call fails."""


class LLMClient:
    """Lightweight LLM client using OpenAI-compatible chat completions API."""

    def __init__(self) -> None:
        self.base_url = os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1")
        self.api_key = os.environ.get("LLM_API_KEY", os.environ.get("OPENAI_API_KEY", ""))
        self.default_model = os.environ.get("LLM_MODEL", "gpt-4o-mini")

    async def chat(self, messages: list[dict[str, Any]], model: str | None = None, **kwargs: Any) -> str:
        """Send messages and return the assistant content string.

        Raises:
            LLMError: If the API key is missing or the API call fails.
        """
        if not self.api_key:
            raise LLMError("No API key configured — set LLM_API_KEY or OPENAI_API_KEY")

        url = f"{self.base_url}/chat/completions"
        payload: dict[str, Any] = {
            "model": model or self.default_model,
            "messages": messages,
            "temperature": kwargs.get("temperature", 0.7),
            "max_tokens": kwargs.get("max_tokens", 1024),
        }

        async with httpx.AsyncClient(timeout=60.0) as client:
            try:
                resp = await client.post(
                    url,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()
                return data["choices"][0]["message"]["content"]
            except httpx.HTTPStatusError as exc:
                raise LLMError(f"HTTP {exc.response.status_code}: {exc.response.text[:200]}") from exc
            except httpx.RequestError as exc:
                raise LLMError(f"Request failed: {exc}") from exc


_llm_client = LLMClient()

app = FastAPI(title="Agent OS — Orchestrator", version="0.2.0", redirect_slashes=False)

# ── Global services ──────────────────────────────────────────────

_agents: dict[str, dict[str, Any]] = {}

# Try PostgresStore when DATABASE_URL is set, fall back to InMemoryStore
_pg_store: Any | None = None
_database_url = os.environ.get("DATABASE_URL", "")
if _database_url:
    try:
        from src.memory.pgstore import PostgresStore
        _pg_store = PostgresStore(_database_url)

        @app.on_event("startup")
        async def _init_pg_store() -> None:
            await _pg_store.initialize()  # type: ignore[union-attr]
            for agent in await _pg_store.list_agents():  # type: ignore[union-attr]
                _agents[agent["id"]] = agent

    except Exception:
        _pg_store = None


@app.on_event("startup")
async def _init_default_agent() -> None:
    """Auto-create a default agent if none exist."""
    if _agents:
        return
    agent_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    default_agent = {
        "id": agent_id,
        "name": "默认助手",
        "description": "Agent OS 默认智能助手，开箱即用",
        "status": "idle",
        "model": os.environ.get("LLM_MODEL", "glm-4-flash"),
        "system_prompt": "你是 Agent OS 的默认助手。你善于用中文回答各类问题，提供有帮助的建议。回答要简洁明了。",
        "tools": [],
        "temperature": 0.7,
        "max_tokens": 4096,
        "created_at": now,
        "updated_at": now,
    }
    _agents[agent_id] = default_agent
    if _pg_store is not None:
        await _pg_store.store_agent(default_agent)
    await _memory_service.init_agent_blocks(agent_id)
    print(f"Default agent initialized: {agent_id}")


@app.on_event("startup")
async def _start_forgetting_sweep() -> None:
    """Run forgetting sweep periodically (daily)."""

    async def _sweep_loop():
        while True:
            await asyncio.sleep(86400)  # 24 hours
            try:
                for agent_id in list(_agents.keys()):
                    forget_result = await _active_forgetting.run_sweep(agent_id=agent_id)
                    # Issue 2: Emit forget event
                    _emit_memory_event("forget", {
                        "agent_id": agent_id,
                        "scanned": forget_result.scanned,
                        "archived": forget_result.archived,
                        "archived_ids": forget_result.archived_ids[:10],  # cap for SSE
                    })
                    # Episodic → Semantic migration on each sweep
                    migrate_ids = await _memory_migrator.migrate_episodic_to_semantic(agent_id)
                    # Issue 2: Emit migrate event
                    _emit_memory_event("migrate", {
                        "agent_id": agent_id,
                        "path": "episodic_to_semantic",
                        "count": len(migrate_ids),
                        "ids": migrate_ids[:10],
                    })
            except Exception:
                pass  # Don't crash the loop

    asyncio.create_task(_sweep_loop())


@app.on_event("shutdown")
async def _shutdown() -> None:
    """Graceful shutdown: persist FAISS index, close database connections."""
    _vector_store.save()
    if _pg_store is not None:
        await _pg_store.close()
    await _communication_bus.close()

# Ensure data directory exists for SQLite databases
Path("data").mkdir(exist_ok=True)

# ── Global services (ordered to satisfy dependencies) ────────────

# Knowledge graph must be created first — MemoryService depends on it.
_knowledge_graph = KnowledgeGraph()

# Embedding provider and vector store for semantic search
_embedding_provider = SentenceTransformerProvider()
_vector_store = FAISSVectorStore(provider=_embedding_provider)

# Memory service with SQLiteStore for persistence
_memory_service = MemoryService(
    SQLiteStore(), vector_store=_vector_store, knowledge_graph=_knowledge_graph
)

# Context compression components
_context_monitor = ContextMonitor()
_async_compressor = AsyncCompressor(monitor=_context_monitor)
_sync_compressor = SyncCompressor(monitor=_context_monitor)

# Memory migration and active forgetting
_memory_migrator = MemoryMigrator(_memory_service)
_active_forgetting = ActiveForgetting(_memory_service)

_context_manager = ContextManager(_memory_service)
_context_compiler = ContextCompiler(_context_manager)
_tool_executor = ToolExecutor(ToolRegistry())
_communication_bus = CommunicationBus()
_concurrency_controller = ConcurrencyController()

# ── Memory Event Bus (Issue 2: debug event stream) ───────────────
# A lightweight pub/sub so memory subsystems (compress, forget, migrate)
# can emit events that are pushed to the frontend via SSE.
_memory_event_subscribers: list[asyncio.Queue[str]] = []


def _emit_memory_event(event: str, details: dict[str, Any]) -> None:
    """Broadcast a memory lifecycle event to all SSE subscribers.

    Non-blocking: puts into each subscriber's queue. Queues that are
    full are silently skipped to avoid back-pressure issues.

    Args:
        event: Event type — ``"compress"``, ``"forget"``, or ``"migrate"``.
        details: Arbitrary payload describing the event.
    """
    sse_msg = _sse("memory_event", {"event": event, **details})
    dead: list[asyncio.Queue[str]] = []
    for q in _memory_event_subscribers:
        try:
            q.put_nowait(sse_msg)
        except asyncio.QueueFull:
            dead.append(q)
    for q in dead:
        _memory_event_subscribers.remove(q)


def subscribe_memory_events() -> asyncio.Queue[str]:
    """Register a queue to receive memory lifecycle SSE events.

    Returns:
        An :class:`asyncio.Queue` that will receive SSE-formatted strings.
    """
    q: asyncio.Queue[str] = asyncio.Queue(maxsize=200)
    _memory_event_subscribers.append(q)
    return q


def unsubscribe_memory_events(q: asyncio.Queue[str]) -> None:
    """Remove a previously subscribed event queue."""
    if q in _memory_event_subscribers:
        _memory_event_subscribers.remove(q)

# Execution log for debug/replay (in-memory, capped)
_execution_log: list[dict[str, Any]] = []
_MAX_EXECUTION_LOG = 1000


# ── Request models ───────────────────────────────────────────────


class CreateAgentRequest(BaseModel):
    name: str = "New Agent"
    description: str = ""
    model: str = "glm-4-flash"
    system_prompt: str = ""
    tools: list[str] = []


class ExecuteRequest(BaseModel):
    agent_id: str
    input: str
    session_id: str = ""


class StoreMemoryRequest(BaseModel):
    content: str
    agent_id: str = ""
    session_id: str = ""
    memory_type: str = "session"
    scope: str = "agent"
    importance: float = 0.5


class ChatRequest(BaseModel):
    message: str
    agent_id: str = ""
    session_id: str = ""


class SendMessageRequest(BaseModel):
    sender_id: str
    recipient_id: str | None = None
    session_id: str = ""
    workspace_id: str = ""
    content: str
    message_type: str = "task"
    priority: int = 1


class GrantPermissionRequest(BaseModel):
    grantor_id: str
    grantee_id: str
    target_agent_id: str
    level: int = 2
    expires_at: str | None = None


# ── Health ───────────────────────────────────────────────────────


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


# ── Simple Chat ──────────────────────────────────────────────────


@app.post("/chat")
async def chat(req: ChatRequest) -> dict:
    """Simple chat endpoint — auto-picks the first agent if none specified."""
    from fastapi.responses import JSONResponse

    agent_id = req.agent_id
    if not agent_id:
        if not _agents:
            return JSONResponse({"error": "No agents available"}, status_code=404)
        agent_id = next(iter(_agents))

    agent = _agents.get(agent_id)
    if not agent:
        return JSONResponse({"error": "Agent not found"}, status_code=404)

    session_id = req.session_id or str(uuid.uuid4())
    system_prompt = agent.get("system_prompt") or "You are a helpful assistant."

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": req.message},
    ]

    try:
        response = await _llm_client.chat(
            messages,
            model=agent.get("model"),
            temperature=agent.get("temperature", 0.7),
            max_tokens=agent.get("max_tokens", 4096),
        )
    except LLMError as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)

    # Store as session memory
    await _memory_service.store(
        content=f"User: {req.message}\nAssistant: {response}",
        agent_id=agent_id,
        session_id=session_id,
        memory_type=MemoryType.SESSION,
        scope=MemoryScope.AGENT,
    )

    # Trigger Session→Episodic migration in background
    async def _migrate_and_emit() -> None:
        ids = await _memory_migrator.migrate_session_to_episodic(session_id, agent_id)
        if ids:
            _emit_memory_event("migrate", {
                "agent_id": agent_id,
                "path": "session_to_episodic",
                "count": len(ids),
                "ids": ids[:10],
            })

    asyncio.create_task(_migrate_and_emit())

    # Fire-and-forget KG entity extraction (non-blocking)
    _trigger_kg_extraction(
        user_message=req.message,
        assistant_response=response,
        session_id=session_id,
    )

    return {"response": response, "agent_id": agent_id, "session_id": session_id}


# ── Agent CRUD ───────────────────────────────────────────────────


@app.post("/agents")
async def create_agent(req: CreateAgentRequest) -> dict:
    agent_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    agent = {
        "id": agent_id,
        "name": req.name,
        "description": req.description,
        "status": "idle",
        "model": req.model,
        "system_prompt": req.system_prompt,
        "tools": req.tools,
        "created_at": now,
        "updated_at": now,
    }
    _agents[agent_id] = agent
    if _pg_store is not None:
        await _pg_store.store_agent(agent)
    await _memory_service.init_agent_blocks(agent_id)
    return agent


@app.get("/agents")
async def list_agents() -> list[dict]:
    return list(_agents.values())


@app.get("/agents/{agent_id}")
async def get_agent(agent_id: str) -> dict:
    agent = _agents.get(agent_id)
    if not agent:
        from fastapi.responses import JSONResponse
        return JSONResponse({"error": "Agent not found"}, status_code=404)
    return agent


@app.delete("/agents/{agent_id}")
async def delete_agent(agent_id: str) -> dict:
    if agent_id not in _agents:
        from fastapi.responses import JSONResponse
        return JSONResponse({"error": "Agent not found"}, status_code=404)
    del _agents[agent_id]
    if _pg_store is not None:
        await _pg_store.delete_agent(agent_id)
    return {"deleted": True}


# ── Memory API (Phase 9) ─────────────────────────────────────────


@app.post("/memories")
async def store_memory(req: StoreMemoryRequest) -> dict:
    """Store a new memory item."""
    ref = await _memory_service.store(
        content=req.content,
        agent_id=req.agent_id,
        session_id=req.session_id,
        memory_type=MemoryType(req.memory_type),
        scope=MemoryScope(req.scope),
        importance=req.importance,
    )
    return {"id": ref.id, "memory_type": ref.memory_type.value, "scope": ref.scope.value}


@app.get("/memories")
async def list_memories(
    agent_id: str = "",
    session_id: str = "",
    memory_type: str = "",
    limit: int = Query(default=100, le=500),
) -> list[dict]:
    """List memories with optional filters."""
    items = await _memory_service.recall(
        query="",
        agent_id=agent_id,
        session_id=session_id,
        memory_type=MemoryType(memory_type) if memory_type else None,
        top_k=limit,
    )
    return [
        {
            "id": m.id,
            "agent_id": m.agent_id,
            "session_id": m.session_id,
            "memory_type": m.memory_type.value,
            "scope": m.scope.value,
            "content": m.content,
            "importance": m.importance,
            "created_at": m.created_at,
            "archived": m.archived,
        }
        for m in items
    ]


@app.get("/memories/layers")
async def memory_layers(agent_id: str = "") -> dict:
    """Get memory layer statistics for an agent."""
    items = await _memory_service.recall(
        query="",
        agent_id=agent_id,
        top_k=1000,
    )
    stats: dict[str, int] = {"working": 0, "session": 0, "episodic": 0, "semantic": 0}
    for m in items:
        if not m.archived and m.memory_type.value in stats:
            stats[m.memory_type.value] += 1
    return stats


@app.delete("/memories/{memory_id}")
async def delete_memory(memory_id: str) -> dict:
    """Delete a memory item."""
    deleted = await _memory_service.delete(memory_id)
    return {"deleted": deleted}


# ── Permission API (Phase 8) ──────────────────────────────────────


@app.post("/permissions/grant")
async def grant_permission(req: GrantPermissionRequest) -> dict:
    """Grant memory access permission between agents."""
    grant_id = _memory_service.grant_access(
        grantor_id=req.grantor_id,
        grantee_id=req.grantee_id,
        target_agent_id=req.target_agent_id,
        level=req.level,
        expires_at=req.expires_at,
    )
    return {"grant_id": grant_id, "level": req.level}


@app.get("/permissions/log")
async def permission_log(
    accessor_id: str = "",
    target_agent_id: str = "",
    limit: int = Query(default=50, le=500),
) -> list[dict]:
    """Get memory access log."""
    entries = _memory_service.get_access_log(
        accessor_id=accessor_id,
        target_agent_id=target_agent_id,
        limit=limit,
    )
    return [
        {
            "id": e.id,
            "accessor_id": e.accessor_id,
            "target_agent_id": e.target_agent_id,
            "memory_id": e.memory_id,
            "action": e.action,
            "level_granted": e.level_granted.value,
            "granted_at": e.granted_at,
        }
        for e in entries
    ]


# ── Communication API (Phase 8) ──────────────────────────────────


@app.post("/messages")
async def send_message(req: SendMessageRequest) -> dict:
    """Send a message between agents."""
    msg = AgentMessage(
        sender_id=req.sender_id,
        recipient_id=req.recipient_id,
        session_id=req.session_id,
        workspace_id=req.workspace_id,
        content=req.content,
        message_type=MessageType(req.message_type),
        priority=MessagePriority(req.priority),
    )

    if req.recipient_id:
        msg_id = await _communication_bus.send(msg)
    else:
        ids = await _communication_bus.broadcast(msg, session_id=req.session_id)
        return {"broadcast": True, "delivered_ids": ids, "count": len(ids)}

    return {"id": msg_id, "status": "delivered"}


@app.get("/messages")
async def list_messages(
    agent_id: str = "",
    session_id: str = "",
    limit: int = Query(default=50, le=500),
) -> list[dict]:
    """Get message history for an agent or session."""
    if agent_id:
        history = _communication_bus.get_history(agent_id, limit=limit)
        return [m.to_dict() for m in history]
    return []


# ── Knowledge Graph API (Phase 9) ────────────────────────────────


@app.get("/kg/entities")
async def search_entities(q: str = "", entity_type: str = "", limit: int = Query(default=20, le=500)) -> list[dict]:
    """Search entities in the knowledge graph."""
    if q:
        return _knowledge_graph.search_entities(q, entity_type=entity_type or None, limit=limit)
    return []


@app.get("/kg/expand")
async def expand_entity(name: str, depth: int = 2) -> dict:
    """Expand the neighbourhood around an entity."""
    return _knowledge_graph.expand(name, depth=depth)


@app.get("/kg/stats")
async def kg_stats() -> dict:
    """Knowledge graph statistics."""
    return _knowledge_graph.stats()


# ── Debug API (Phase 9) ──────────────────────────────────────────


@app.get("/debug/history")
async def debug_history(
    agent_id: str = "",
    session_id: str = "",
    limit: int = Query(default=50, le=500),
) -> list[dict]:
    """Get execution history for debugging and replay."""
    entries = _execution_log
    if agent_id:
        entries = [e for e in entries if e.get("agent_id") == agent_id]
    if session_id:
        entries = [e for e in entries if e.get("session_id") == session_id]
    return list(reversed(entries[-limit:]))


@app.get("/debug/status")
async def debug_status() -> dict:
    """Get overall system debug status."""
    return {
        "agents": len(_agents),
        "concurrency": _concurrency_controller.get_status(),
        "kg_stats": _knowledge_graph.stats(),
    }


# ── Node handler functions (used by FunctionNode) ────────────────

# Pattern to detect structured tool invocations in LLM output.
_TOOL_INVOCATION_RE = _re.compile(
    r'\b(?:tool_call|function_call|action)\s*[:=]\s*["\']?(\w+)',
    _re.IGNORECASE,
)


def _has_tool_invocation(text: str) -> bool:
    """Check if the LLM response contains a structured tool invocation."""
    return bool(_TOOL_INVOCATION_RE.search(text))


def _log_execution_step(
    node_name: str,
    state: GraphState,
    status: str = "done",
) -> None:
    """Append a step to the execution log (capped at _MAX_EXECUTION_LOG)."""
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
    _execution_log.append(entry)
    if len(_execution_log) > _MAX_EXECUTION_LOG:
        del _execution_log[: len(_execution_log) - _MAX_EXECUTION_LOG]


def _trigger_kg_extraction(
    user_message: str,
    assistant_response: str,
    session_id: str = "",
) -> None:
    """Fire-and-forget KG entity extraction for a completed turn.

    Runs via ``asyncio.create_task`` so it never blocks the response.
    Exceptions are logged and silently swallowed.

    Args:
        user_message: The user's original input text.
        assistant_response: The assistant's reply text.
        session_id: Session identifier for provenance.
    """
    try:
        if _knowledge_graph is not None and hasattr(_knowledge_graph, "extract_and_ingest"):
            text = f"User: {user_message}\nAssistant: {assistant_response}"
            # Synchronous method — offload to thread to avoid blocking
            asyncio.create_task(
                asyncio.to_thread(
                    _knowledge_graph.extract_and_ingest,
                    text=text,
                    memory_id=session_id,
                )
            )
    except Exception:
        logger.warning("KG extraction failed", exc_info=True)


async def _node_start(state: GraphState) -> GraphState:
    """Initialize the execution pipeline."""
    state.messages.append({"role": "system", "content": "Processing started"})
    state.context["original_input"] = state.input
    state.current_node = "start"
    state.output = "started"
    _log_execution_step("start", state)
    return state


async def _node_llm(state: GraphState) -> GraphState:
    """LLM processing with real API call and memory integration."""
    agent_id = state.agent_id
    session_id = state.session_id
    user_input = state.input

    # Resolve model: agent config > env default
    agent = _agents.get(agent_id)
    agent_model = agent.get("model") if agent else None

    # Use ContextCompiler to assemble messages (memory, tools, conversation)
    conversation = list(state.messages) + [{"role": "user", "content": user_input}]
    system_prompt = (agent.get("system_prompt") if agent else None) or "You are a helpful assistant."
    llm_messages = await _context_compiler.compile(
        system_prompt=system_prompt,
        conversation=conversation,
        agent_id=agent_id,
        session_id=session_id,
    )

    try:
        response = await _llm_client.chat(llm_messages, model=agent_model)
    except LLMError as exc:
        state.errors.append(f"LLM error: {exc}")
        state.output = f"[LLM unavailable] {exc}"
        state.current_node = "llm"
        _log_execution_step("llm", state, status="error")
        return state

    state.messages.append({"role": "user", "content": user_input})
    state.messages.append({"role": "assistant", "content": response})
    state.output = response
    state.current_node = "llm"

    # Store via migration (avoids duplicate — migrate stores once internally)
    from src.memory.types import MemoryItem
    working_item = MemoryItem(
        content=f"User: {user_input}\nAssistant: {response}",
        agent_id=agent_id,
        session_id=session_id,
        memory_type=MemoryType.WORKING,
        scope=MemoryScope.AGENT,
    )
    await _memory_migrator.migrate_working_to_session(working_item, session_id, agent_id)
    state.memory_refs.append(working_item.id)

    # Extract entities for knowledge graph (non-blocking)
    _trigger_kg_extraction(
        user_message=user_input,
        assistant_response=response,
        session_id=working_item.id,
    )

    # Estimate context token usage and trigger compression if needed
    total_tokens = sum(len(m.get("content", "")) // 4 for m in state.messages)
    trigger_level = _context_monitor.check_trigger(total_tokens)

    if trigger_level == CompressionLevel.SYNC:
        # Synchronous compression with 2s timeout
        try:
            items = await _memory_service.recall(
                query="", agent_id=agent_id, session_id=session_id, top_k=50
            )
            result = await _sync_compressor.compress(items)
            # Persist summary items and record their refs
            for summary_item in result.summaries:
                await _memory_service.store(
                    content=summary_item.content,
                    agent_id=summary_item.agent_id,
                    session_id=summary_item.session_id,
                    memory_type=summary_item.memory_type,
                    scope=summary_item.scope,
                    importance=summary_item.importance,
                    metadata=summary_item.metadata,
                )
                state.memory_refs.append(summary_item.id)
            # Archive source items that were replaced by summaries
            if result.summaries:
                retained_ids = {r.id for r in result.retained}
                for item in items:
                    if item.id not in retained_ids:
                        await _memory_service.update(
                            item.id, accessor_id=agent_id, archived=True,
                        )
        except asyncio.TimeoutError:
            pass  # Skip compression if timeout
        else:
            # Issue 2: Emit compress event after sync compression
            _emit_memory_event("compress", {
                "agent_id": agent_id,
                "level": "sync",
                "original_count": result.original_count,
                "retained_count": result.compressed_count,
                "summary_count": len(result.summaries),
            })
    elif trigger_level == CompressionLevel.ASYNC:
        # Asynchronous compression - non-blocking
        items = await _memory_service.recall(
            query="", agent_id=agent_id, session_id=session_id, top_k=50
        )

        async def _on_compressed(retained: list, summaries: list) -> None:
            """Callback: persist summaries, archive replaced sources, emit event."""
            for summary_item in summaries:
                await _memory_service.store(
                    content=summary_item.content,
                    agent_id=summary_item.agent_id,
                    session_id=summary_item.session_id,
                    memory_type=summary_item.memory_type,
                    scope=summary_item.scope,
                    importance=summary_item.importance,
                    metadata=summary_item.metadata,
                )
            retained_ids = {r.id for r in retained}
            for item in items:
                if item.id not in retained_ids:
                    await _memory_service.update(
                        item.id, accessor_id=agent_id, archived=True,
                    )
            # Issue 2: Emit compress event after async compression
            _emit_memory_event("compress", {
                "agent_id": agent_id,
                "level": "async",
                "original_count": len(items),
                "retained_count": len(retained),
                "summary_count": len(summaries),
            })

        await _async_compressor.trigger(items, on_compressed=_on_compressed)

    # Determine if tool use is needed:
    # 1. Explicit tool_call in context (set by upstream)
    # 2. LLM response contains structured tool invocation pattern
    if state.context.get("tool_call") or _has_tool_invocation(response):
        state.context["needs_tool"] = True
    else:
        state.context["needs_tool"] = False

    _log_execution_step("llm", state)
    return state


async def _node_tool(state: GraphState) -> GraphState:
    """Execute a tool call via ToolExecutor."""
    tool_name = state.context.get("tool_call", "web_search")
    tool_args = state.context.get("tool_args", {"query": state.input})

    result = await _tool_executor.execute(tool_name, tool_args)

    if result["status"] != "success":
        error_msg = result.get("error", "Unknown error")
        state.context["tool_result"] = f"[Tool error] {tool_name}: {error_msg}"
    else:
        state.context["tool_result"] = str(result["output"])

    state.tool_results.append({"tool": tool_name, "result": state.context["tool_result"]})

    # Store tool result with safety deadline
    result_preview = str(result["output"])[:200] if result["status"] == "success" else error_msg
    await _memory_service.store(
        content=f"Tool {tool_name} result: {result_preview}",
        agent_id=state.agent_id,
        session_id=state.session_id,
        memory_type=MemoryType.WORKING,
        scope=MemoryScope.AGENT,
        metadata={"safety_deadline": True, "tool_result": True},
    )

    state.current_node = "tool"
    _log_execution_step("tool", state)
    return state


async def _node_llm_synthesize(state: GraphState) -> GraphState:
    """LLM synthesizes tool results into final answer.

    Uses ContextCompiler when available for richer context assembly.
    Falls back to direct prompt construction when ContextCompiler
    is unavailable (graceful degradation).

    After synthesis, triggers async KG entity extraction so entities
    from the tool-result synthesis flow are captured.
    """
    tool_result = state.context.get("tool_result", "")

    # Resolve model: agent config > env default
    agent = _agents.get(state.agent_id)
    agent_model = agent.get("model") if agent else None
    system_prompt = (agent.get("system_prompt") if agent else None) or "You are a helpful assistant."

    # ── Issue 4: Use ContextCompiler when available ──────────
    if _context_compiler is not None:
        conversation = list(state.messages) + [
            {"role": "user", "content": state.input},
            {"role": "system", "content": f"Tool results: {tool_result}"},
        ]
        messages = await _context_compiler.compile(
            system_prompt=f"{system_prompt}\n\nSynthesize the tool results into a final answer for the user.",
            conversation=conversation,
            agent_id=state.agent_id,
            session_id=state.session_id,
        )
    else:
        # Fallback: direct prompt construction
        messages = [
            {"role": "system", "content": "Synthesize the tool results into a final answer for the user."},
            {"role": "user", "content": f"Original question: {state.input}\n\nTool results: {tool_result}"},
        ]

    try:
        response = await _llm_client.chat(messages, model=agent_model)
    except LLMError as exc:
        state.errors.append(f"LLM synthesize error: {exc}")
        state.output = state.context.get("tool_result", "[no result]")
        state.current_node = "llm_synthesize"
        _log_execution_step("llm_synthesize", state, status="error")
        return state

    state.messages.append({"role": "assistant", "content": response})
    state.output = response
    state.current_node = "llm_synthesize"

    # ── Issue 1: Async KG extraction from synthesis path ─────
    _trigger_kg_extraction(
        user_message=state.input,
        assistant_response=response,
        session_id=state.session_id,
    )

    _log_execution_step("llm_synthesize", state)
    return state


# ── Graph builder ────────────────────────────────────────────────


def _build_execution_graph() -> StateGraph:
    """Build the orchestration graph with nodes and edges.

    Graph topology::

        start → llm → [needs_tool?] → tool → llm_synthesize → end
                         ↓ no
                        end
    """
    graph = StateGraph("exec-graph")
    graph.set_checkpoint_store(InMemoryCheckpointStore())

    graph.add_node("start", FunctionNode("start", _node_start))
    graph.add_node("llm", FunctionNode("llm", _node_llm))
    graph.add_node("tool", FunctionNode("tool", _node_tool))
    graph.add_node("llm_synthesize", FunctionNode("llm_synthesize", _node_llm_synthesize))

    graph.add_edge("start", "llm")
    graph.add_edge("tool", "llm_synthesize")

    graph.add_conditional_edge(
        source="llm",
        targets={
            "tool": "tool",
            "__default__": "",
        },
        condition=lambda state: "tool" if state.context.get("needs_tool") else "__default__",
    )

    graph.set_entry_point("start")
    return graph


# ── Execution (SSE) ─────────────────────────────────────────────


@app.post("/execute")
async def execute(req: ExecuteRequest) -> StreamingResponse:
    """Execute an agent graph and stream node status via SSE."""
    agent = _agents.get(req.agent_id)
    if not agent:
        from fastapi.responses import JSONResponse
        return JSONResponse({"error": "Agent not found"}, status_code=404)

    session_id = req.session_id or str(uuid.uuid4())
    await _memory_service.create_session(session_id, req.agent_id)

    graph = _build_execution_graph()
    initial_state = GraphState(
        input=req.input,
        agent_id=req.agent_id,
        session_id=session_id,
    )

    event_queue: asyncio.Queue[str | None] = asyncio.Queue()

    async def on_node_complete(node_name: str, state: GraphState) -> None:
        """Callback fired after each graph node completes.

        Publishes node status to the CommunicationBus so other agents
        in the same session can observe execution progress.
        """
        # Publish to communication bus for multi-agent awareness
        try:
            msg = AgentMessage(
                sender_id=req.agent_id,
                recipient_id=None,  # broadcast
                session_id=session_id,
                content=f"Node {node_name} completed: {state.output[:100] if state.output else ''}",
                message_type=MessageType.NOTIFICATION,
            )
            await _communication_bus.broadcast(msg, session_id=session_id)
        except Exception:
            pass  # Don't let bus errors break execution

        if node_name == "start":
            await event_queue.put(_sse("node_start", {"node": "start", "status": "running"}))
            await event_queue.put(_sse("node_complete", {
                "node": "start", "status": "done", "output": state.output or "started",
            }))
        elif node_name == "llm":
            await event_queue.put(_sse("node_start", {"node": "llm", "status": "running"}))
            await event_queue.put(_sse("node_complete", {
                "node": "llm", "status": "done", "output": state.output,
            }))
        elif node_name == "tool":
            await event_queue.put(_sse("node_start", {"node": "tool", "status": "running"}))
            await event_queue.put(_sse("node_complete", {
                "node": "tool", "status": "done",
                "output": state.context.get("tool_result", ""),
            }))
        elif node_name == "llm_synthesize":
            await event_queue.put(_sse("node_start", {"node": "llm_synthesize", "status": "running"}))
            await event_queue.put(_sse("node_complete", {
                "node": "llm_synthesize", "status": "done", "output": state.output,
            }))

    async def event_stream() -> AsyncGenerator[str, None]:
        await _concurrency_controller.acquire_agent_slot(req.agent_id)
        agent["status"] = "running"
        yield _sse("agent_status", {"agent_id": req.agent_id, "status": "running"})

        # Subscribe to memory events (compress/forget/migrate) for this SSE stream
        mem_event_q = subscribe_memory_events()

        async def run_graph():
            try:
                final_state = await graph.run(initial_state, on_node_complete=on_node_complete)
                agent["status"] = "idle"
                await event_queue.put(_sse("agent_status", {"agent_id": req.agent_id, "status": "idle"}))
                await event_queue.put(_sse("execution_complete", {
                    "output": final_state.output,
                    "session_id": final_state.session_id,
                    "memory_count": len(final_state.memory_refs),
                }))
            except Exception as exc:
                agent["status"] = "idle"
                await event_queue.put(_sse("error", {"message": str(exc)}))
            finally:
                await _concurrency_controller.release_agent_slot(req.agent_id)
                unsubscribe_memory_events(mem_event_q)
                await event_queue.put(None)

        task = asyncio.create_task(run_graph())

        while True:
            # Forward any pending memory events into the main SSE queue
            while not mem_event_q.empty():
                try:
                    await event_queue.put(mem_event_q.get_nowait())
                except asyncio.QueueEmpty:
                    break
            item = await event_queue.get()
            if item is None:
                break
            yield item

        yield "data: [DONE]\n\n"
        await task

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ── SSE helpers ──────────────────────────────────────────────────


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


async def _sse_error(msg: str) -> AsyncGenerator[str, None]:
    yield _sse("error", {"message": msg})
    yield "data: [DONE]\n\n"


# ── CLI entry point ──────────────────────────────────────────────


def main() -> None:
    """CLI entry point: run the minimal graph demo."""

    async def run_demo():
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

        memories = await _memory_service.recall(
            query="", agent_id="cli", session_id="cli-session", top_k=10,
        )
        print(f"\n=== {len(memories)} memories stored ===")
        print(f"=== KG stats: {_knowledge_graph.stats()} ===")

    asyncio.run(run_demo())


if __name__ == "__main__":
    main()
