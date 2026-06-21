"""Chat and Execute SSE routes — includes graph execution pipeline."""

from __future__ import annotations

import asyncio
import json
import logging
import re as _re
import uuid
from typing import Any, AsyncGenerator

from fastapi import APIRouter
from fastapi.responses import JSONResponse, StreamingResponse

from src.api.models import ChatRequest, ExecuteRequest
from src.graph import StateGraph, GraphState, InMemoryCheckpointStore
from src.graph.nodes import FunctionNode
from src.memory import MemoryType, MemoryScope
from src.memory.event_bus import EventType
from src.memory.hooks import (
    SessionContext,
    TurnContext,
    CompressContext,
    IngestContext,
)
from src.memory.types import MemoryItem, MemoryOrigin
from src.communication.message import AgentMessage, MessageType
from src.services import _state
from src.services.llm_client import LLMClient, LLMError

logger = logging.getLogger(__name__)

router = APIRouter()


# ── SSE helpers ────────────────────────────────────────────────────


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


async def _sse_error(msg: str) -> AsyncGenerator[str, None]:
    yield _sse("error", {"message": msg})
    yield "data: [DONE]\n\n"


# ── Tool invocation detection ─────────────────────────────────────

_TOOL_INVOCATION_RE = _re.compile(
    r'\b(?:tool_call|function_call|action)\s*[:=]\s*["\']?(\w+)',
    _re.IGNORECASE,
)


def _has_tool_invocation(text: str) -> bool:
    return bool(_TOOL_INVOCATION_RE.search(text))


# ── KG extraction helper ──────────────────────────────────────────


def _trigger_kg_extraction(
    user_message: str,
    assistant_response: str,
    session_id: str = "",
) -> None:
    """Fire-and-forget KG entity extraction for a completed turn."""
    try:
        if _state.knowledge_graph is not None and hasattr(_state.knowledge_graph, "extract_and_ingest"):
            text = f"User: {user_message}\nAssistant: {assistant_response}"
            asyncio.create_task(
                asyncio.to_thread(
                    _state.knowledge_graph.extract_and_ingest,
                    text=text,
                    memory_id=session_id,
                )
            )
    except Exception:
        logger.warning("KG extraction failed", exc_info=True)


def _fire_write(agent_id: str, coro_fn: Any) -> None:
    """Fire-and-forget a memory write via the write queue when configured
    (per-agent concurrency pool + drain coverage at shutdown), else a bare
    ``asyncio.create_task`` (legacy path — e.g. when the queue is not yet
    wired in unit tests)."""
    wq = _state.write_queue
    if wq is not None:
        wq.fire(agent_id, coro_fn)
    else:
        asyncio.create_task(coro_fn())


def _trigger_ingest(
    memory_id: str,
    content: str,
    agent_id: str,
    session_id: str,
    origin: MemoryOrigin,
) -> None:
    """Fire-and-forget ① IngestorAgent via ``EventType.INGEST``.

    Runs as an independent ``asyncio.create_task`` so the request hot-path
    never waits on LLM extraction. No-op when the MEMORY_INGESTOR_ENABLED
    feature gate is off (no INGEST hook registered → bus.emit returns None).
    The P0 FOREGROUND red-line is enforced inside the hook.
    """
    try:
        ctx = IngestContext(
            memory_id=memory_id,
            content=content,
            agent_id=agent_id,
            session_id=session_id,
            origin=origin.value if isinstance(origin, MemoryOrigin) else str(origin),
        )
        _fire_write(agent_id, lambda: _state.memory_event_bus.emit(EventType.INGEST, ctx))
    except Exception:
        logger.warning("INGEST trigger failed", exc_info=True)


# ── Graph node handlers ───────────────────────────────────────────


async def _node_start(state: GraphState) -> GraphState:
    """Initialize the execution pipeline."""
    state.messages.append({"role": "system", "content": "Processing started"})
    state.context["original_input"] = state.input
    state.current_node = "start"
    state.output = "started"
    _state.log_execution_step("start", state)
    return state


async def _node_llm(state: GraphState) -> GraphState:
    """LLM processing with real API call and memory integration."""
    agent_id = state.agent_id
    session_id = state.session_id
    user_input = state.input

    agent = _state.agents.get(agent_id)
    agent_model = agent.get("model") if agent else None

    conversation = list(state.messages) + [{"role": "user", "content": user_input}]
    system_prompt = (agent.get("system_prompt") if agent else None) or "You are a helpful assistant."
    compiled = await _state.context_compiler.compile(
        system_prompt=system_prompt,
        conversation=conversation,
        agent_id=agent_id,
        session_id=session_id,
        cache_breakpoint=True,
    )
    llm_messages = compiled.messages

    try:
        response = await _state.llm_client.chat(
            llm_messages, model=agent_model, static_count=compiled.static_count,
        )
    except LLMError as exc:
        state.errors.append(f"LLM error: {exc}")
        state.output = f"[LLM unavailable] {exc}"
        state.current_node = "llm"
        _state.log_execution_step("llm", state, status="error")
        return state

    state.messages.append({"role": "user", "content": user_input})
    state.messages.append({"role": "assistant", "content": response})
    state.output = response
    state.current_node = "llm"

    working_item = MemoryItem(
        content=f"User: {user_input}\nAssistant: {response}",
        agent_id=agent_id,
        session_id=session_id,
        memory_type=MemoryType.WORKING,
        scope=MemoryScope.AGENT,
    )
    # migrate working→session via the event bus (was: memory_migrator call)
    await _state.memory_event_bus.emit(
        EventType.TURN_END,
        TurnContext(agent_id=agent_id, session_id=session_id, working_item=working_item),
    )
    state.memory_refs.append(working_item.id)

    # Fire-and-forget LLM semantic ingestion (IngestorAgent): after the
    # core store (TURN_END above) completes, hand the stored memory to the
    # ① side agent for KG entity/relation extraction + importance scoring +
    # identity_category tagging. The hook is a no-op when the
    # MEMORY_INGESTOR_ENABLED feature gate is off (no INGEST hook registered).
    # P0 red-line is enforced inside the agent (origin=FOREGROUND → early
    # return); working_item here is agent-self-sedimented.
    _trigger_ingest(
        memory_id=working_item.id,
        content=working_item.content,
        agent_id=agent_id,
        session_id=session_id,
        origin=MemoryOrigin.AGENT,
    )

    _trigger_kg_extraction(
        user_message=user_input,
        assistant_response=response,
        session_id=working_item.id,
    )

    # Context compression via the event bus (was: inline recall/compress/
    # store/update + emit_memory_event). The hook returns any SYNC summary
    # ids so memory_refs stays in sync; ASYNC fires in the background.
    compress_result = await _state.memory_event_bus.emit(
        EventType.PRE_COMPRESS,
        CompressContext(
            agent_id=agent_id,
            session_id=session_id,
            accessor_id=agent_id,
            messages=list(state.messages),
        ),
    )
    if compress_result is not None and compress_result.summary_ids:
        state.memory_refs.extend(compress_result.summary_ids)

    if state.context.get("tool_call") or _has_tool_invocation(response):
        state.context["needs_tool"] = True
    else:
        state.context["needs_tool"] = False

    _state.log_execution_step("llm", state)
    return state


async def _node_tool(state: GraphState) -> GraphState:
    """Execute a tool call via ToolExecutor."""
    tool_name = state.context.get("tool_call", "web_search")
    tool_args = state.context.get("tool_args", {"query": state.input})

    result = await _state.tool_executor.execute(tool_name, tool_args)

    if result["status"] != "success":
        error_msg = result.get("error", "Unknown error")
        state.context["tool_result"] = f"[Tool error] {tool_name}: {error_msg}"
    else:
        state.context["tool_result"] = str(result["output"])

    state.tool_results.append({"tool": tool_name, "result": state.context["tool_result"]})

    result_preview = str(result["output"])[:200] if result["status"] == "success" else error_msg
    tool_item = MemoryItem(
        content=f"Tool {tool_name} result: {result_preview}",
        agent_id=state.agent_id,
        session_id=state.session_id,
        memory_type=MemoryType.WORKING,
        scope=MemoryScope.AGENT,
        metadata={"safety_deadline": True, "tool_result": True},
    )
    # store tool-result working memory via the event bus
    await _state.memory_event_bus.emit(
        EventType.TURN_END,
        TurnContext(agent_id=state.agent_id, session_id=state.session_id, tool_result_item=tool_item),
    )

    # Fire-and-forget LLM semantic ingestion for the tool-result memory.
    _trigger_ingest(
        memory_id=tool_item.id,
        content=tool_item.content,
        agent_id=state.agent_id,
        session_id=state.session_id,
        origin=MemoryOrigin.AGENT,
    )

    state.current_node = "tool"
    _state.log_execution_step("tool", state)
    return state


async def _node_llm_synthesize(state: GraphState) -> GraphState:
    """LLM synthesizes tool results into final answer."""
    tool_result = state.context.get("tool_result", "")

    agent = _state.agents.get(state.agent_id)
    agent_model = agent.get("model") if agent else None
    system_prompt = (agent.get("system_prompt") if agent else None) or "You are a helpful assistant."

    if _state.context_compiler is not None:
        conversation = list(state.messages) + [
            {"role": "user", "content": state.input},
            {"role": "system", "content": f"Tool results: {tool_result}"},
        ]
        compiled = await _state.context_compiler.compile(
            system_prompt=f"{system_prompt}\n\nSynthesize the tool results into a final answer for the user.",
            conversation=conversation,
            agent_id=state.agent_id,
            session_id=state.session_id,
            cache_breakpoint=True,
        )
        messages = compiled.messages
        static_count: int | None = compiled.static_count
    else:
        messages = [
            {"role": "system", "content": "Synthesize the tool results into a final answer for the user."},
            {"role": "user", "content": f"Original question: {state.input}\n\nTool results: {tool_result}"},
        ]
        static_count = None

    try:
        response = await _state.llm_client.chat(
            messages, model=agent_model, static_count=static_count,
        )
    except LLMError as exc:
        state.errors.append(f"LLM synthesize error: {exc}")
        state.output = state.context.get("tool_result", "[no result]")
        state.current_node = "llm_synthesize"
        _state.log_execution_step("llm_synthesize", state, status="error")
        return state

    state.messages.append({"role": "assistant", "content": response})
    state.output = response
    state.current_node = "llm_synthesize"

    _trigger_kg_extraction(
        user_message=state.input,
        assistant_response=response,
        session_id=state.session_id,
    )

    _state.log_execution_step("llm_synthesize", state)
    return state


# ── Graph builder ─────────────────────────────────────────────────


def _build_execution_graph() -> StateGraph:
    """Build the orchestration graph with nodes and edges."""
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


# ── Health endpoint ────────────────────────────────────────────────


async def _health_impl() -> dict:
    return {"status": "ok"}


# Expose at root level (no /v1 prefix) via engine.py
root_router_health = APIRouter()
root_router_health.add_api_route("/health", _health_impl, methods=["GET"])


# ── Simple Chat ───────────────────────────────────────────────────


@router.post("/chat")
async def chat(req: ChatRequest) -> dict:
    """Simple chat endpoint — auto-picks the first agent if none specified."""
    agent_id = req.agent_id
    if not agent_id:
        if not _state.agents:
            return JSONResponse({"error": "No agents available"}, status_code=404)
        agent_id = next(iter(_state.agents))

    agent = _state.agents.get(agent_id)
    if not agent:
        return JSONResponse({"error": "Agent not found"}, status_code=404)

    session_id = req.session_id or str(uuid.uuid4())
    system_prompt = agent.get("system_prompt") or "You are a helpful assistant."

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": req.message},
    ]

    try:
        response = await _state.llm_client.chat(
            messages,
            model=agent.get("model"),
            temperature=agent.get("temperature", 0.7),
            max_tokens=agent.get("max_tokens", 4096),
        )
    except LLMError as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)

    conversation_item = MemoryItem(
        content=f"User: {req.message}\nAssistant: {response}",
        agent_id=agent_id,
        session_id=session_id,
        memory_type=MemoryType.SESSION,
        scope=MemoryScope.AGENT,
    )
    # store the turn as session memory via the event bus
    await _state.memory_event_bus.emit(
        EventType.TURN_END,
        TurnContext(agent_id=agent_id, session_id=session_id, conversation_item=conversation_item),
    )

    # session→episodic migration, fire-and-forget (was: _migrate_and_emit)
    _fire_write(
        agent_id,
        lambda: _state.memory_event_bus.emit(
            EventType.SESSION_END,
            SessionContext(agent_id=agent_id, session_id=session_id),
        ),
    )

    _trigger_kg_extraction(
        user_message=req.message,
        assistant_response=response,
        session_id=session_id,
    )

    return {"response": response, "agent_id": agent_id, "session_id": session_id}


# ── Execute (SSE) ─────────────────────────────────────────────────


@router.post("/execute")
async def execute(req: ExecuteRequest) -> StreamingResponse:
    """Execute an agent graph and stream node status via SSE."""
    agent = _state.agents.get(req.agent_id)
    if not agent:
        return JSONResponse({"error": "Agent not found"}, status_code=404)

    session_id = req.session_id or str(uuid.uuid4())
    await _state.memory_event_bus.emit(
        EventType.SESSION_START,
        SessionContext(agent_id=req.agent_id, session_id=session_id),
    )

    graph = _build_execution_graph()
    initial_state = GraphState(
        input=req.input,
        agent_id=req.agent_id,
        session_id=session_id,
    )

    event_queue: asyncio.Queue[str | None] = asyncio.Queue()

    async def on_node_complete(node_name: str, state: GraphState) -> None:
        try:
            msg = AgentMessage(
                sender_id=req.agent_id,
                recipient_id=None,
                session_id=session_id,
                content=f"Node {node_name} completed: {state.output[:100] if state.output else ''}",
                message_type=MessageType.NOTIFICATION,
            )
            await _state.communication_bus.broadcast(msg, session_id=session_id)
        except Exception:
            pass

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
        await _state.concurrency_controller.acquire_agent_slot(req.agent_id)
        agent["status"] = "running"
        yield _sse("agent_status", {"agent_id": req.agent_id, "status": "running"})

        mem_event_q = _state.subscribe_memory_events()

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
                # P3: task-post online consolidation (fire-and-forget, non-blocking).
                # Extracts key decisions/pitfalls and writes back via BackwardWriter.
                if _state.task_consolidator is not None and final_state.messages:
                    _fire_write(
                        final_state.agent_id,
                        lambda: _state.task_consolidator.consolidate_task(
                            agent_id=final_state.agent_id,
                            session_id=final_state.session_id,
                            messages=list(final_state.messages),
                        ),
                    )
            except Exception as exc:
                agent["status"] = "idle"
                await event_queue.put(_sse("error", {"message": str(exc)}))
            finally:
                await _state.concurrency_controller.release_agent_slot(req.agent_id)
                _state.unsubscribe_memory_events(mem_event_q)
                await event_queue.put(None)

        task = asyncio.create_task(run_graph())

        while True:
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
