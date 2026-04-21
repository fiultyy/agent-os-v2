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
from src.memory.compressor import CompressionLevel
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
    llm_messages = await _state.context_compiler.compile(
        system_prompt=system_prompt,
        conversation=conversation,
        agent_id=agent_id,
        session_id=session_id,
    )

    try:
        response = await _state.llm_client.chat(llm_messages, model=agent_model)
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

    from src.memory.types import MemoryItem
    working_item = MemoryItem(
        content=f"User: {user_input}\nAssistant: {response}",
        agent_id=agent_id,
        session_id=session_id,
        memory_type=MemoryType.WORKING,
        scope=MemoryScope.AGENT,
    )
    await _state.memory_migrator.migrate_working_to_session(working_item, session_id, agent_id)
    state.memory_refs.append(working_item.id)

    _trigger_kg_extraction(
        user_message=user_input,
        assistant_response=response,
        session_id=working_item.id,
    )

    # Context compression
    total_tokens = sum(len(m.get("content", "")) // 4 for m in state.messages)
    trigger_level = _state.context_monitor.check_trigger(total_tokens)

    if trigger_level == CompressionLevel.SYNC:
        try:
            items = await _state.memory_service.recall(
                query="", agent_id=agent_id, session_id=session_id, top_k=50
            )
            result = await _state.sync_compressor.compress(items)
            for summary_item in result.summaries:
                await _state.memory_service.store(
                    content=summary_item.content,
                    agent_id=summary_item.agent_id,
                    session_id=summary_item.session_id,
                    memory_type=summary_item.memory_type,
                    scope=summary_item.scope,
                    importance=summary_item.importance,
                    metadata=summary_item.metadata,
                )
                state.memory_refs.append(summary_item.id)
            if result.summaries:
                retained_ids = {r.id for r in result.retained}
                for item in items:
                    if item.id not in retained_ids:
                        await _state.memory_service.update(
                            item.id, accessor_id=agent_id, archived=True,
                        )
        except asyncio.TimeoutError:
            pass
        else:
            _state.emit_memory_event("compress", {
                "agent_id": agent_id,
                "level": "sync",
                "original_count": result.original_count,
                "retained_count": result.compressed_count,
                "summary_count": len(result.summaries),
            })
    elif trigger_level == CompressionLevel.ASYNC:
        items = await _state.memory_service.recall(
            query="", agent_id=agent_id, session_id=session_id, top_k=50
        )

        async def _on_compressed(retained: list, summaries: list) -> None:
            for summary_item in summaries:
                await _state.memory_service.store(
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
                    await _state.memory_service.update(
                        item.id, accessor_id=agent_id, archived=True,
                    )
            _state.emit_memory_event("compress", {
                "agent_id": agent_id,
                "level": "async",
                "original_count": len(items),
                "retained_count": len(retained),
                "summary_count": len(summaries),
            })

        await _state.async_compressor.trigger(items, on_compressed=_on_compressed)

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
    await _state.memory_service.store(
        content=f"Tool {tool_name} result: {result_preview}",
        agent_id=state.agent_id,
        session_id=state.session_id,
        memory_type=MemoryType.WORKING,
        scope=MemoryScope.AGENT,
        metadata={"safety_deadline": True, "tool_result": True},
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
        messages = await _state.context_compiler.compile(
            system_prompt=f"{system_prompt}\n\nSynthesize the tool results into a final answer for the user.",
            conversation=conversation,
            agent_id=state.agent_id,
            session_id=state.session_id,
        )
    else:
        messages = [
            {"role": "system", "content": "Synthesize the tool results into a final answer for the user."},
            {"role": "user", "content": f"Original question: {state.input}\n\nTool results: {tool_result}"},
        ]

    try:
        response = await _state.llm_client.chat(messages, model=agent_model)
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

    await _state.memory_service.store(
        content=f"User: {req.message}\nAssistant: {response}",
        agent_id=agent_id,
        session_id=session_id,
        memory_type=MemoryType.SESSION,
        scope=MemoryScope.AGENT,
    )

    async def _migrate_and_emit() -> None:
        ids = await _state.memory_migrator.migrate_session_to_episodic(session_id, agent_id)
        if ids:
            _state.emit_memory_event("migrate", {
                "agent_id": agent_id,
                "path": "session_to_episodic",
                "count": len(ids),
                "ids": ids[:10],
            })

    asyncio.create_task(_migrate_and_emit())

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
    await _state.memory_service.create_session(session_id, req.agent_id)

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
