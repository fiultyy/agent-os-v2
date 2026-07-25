"""Chat and Execute SSE routes — includes graph execution pipeline."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any, AsyncGenerator

from fastapi import APIRouter
from fastapi.responses import JSONResponse, StreamingResponse

from src.api.models import ChatRequest, ExecuteRequest, ExecuteParallelRequest
from src.graph import (
    StateGraph,
    GraphState,
    InMemoryCheckpointStore,
    ParallelNode,
    FanInNode,
)
from src.graph.nodes import FunctionNode, GraphNode
# P2: 复用 P1 产出的 run_agent_turn 作分支调用(已剥离所有 memory 触发 — R1)。
# _build_parallel_graph 不新写 LLM 调用,直接调 run_agent_turn 保证一致性。
from src.agent.meta.agent_runner import run_agent_turn
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
from src.services.llm_client import LLMError

logger = logging.getLogger(__name__)

router = APIRouter()


# ── SSE helpers ────────────────────────────────────────────────────────────


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


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


# ── Parallel graph builder (P2) ────────────────────────────────────


def _build_parallel_graph(
    agent_ids: list[str],
    input: str,
    session_id: str,
) -> StateGraph:
    """Build a multi-perspective parallel graph (P2).

    Topology::

        entry ──▶ multi_perspective(ParallelNode)
                       ├─ branch_<agent_a>: [FunctionNode llm_<a> → run_agent_turn]
                       ├─ branch_<agent_b>: [FunctionNode llm_<b> → run_agent_turn]
                       └─ …
                 ──▶ fan_in(FanInNode, merge_mode="concat")  ──▶ synthesizer?

    Each branch is a single :class:`FunctionNode` whose handler calls
    :func:`run_agent_turn` (the P1 primitive, **already stripped of every
    memory trigger — R1**). We deliberately *do not* write a new
    ``_node_llm_for``: ``run_agent_turn`` builds its own local messages and
    calls the LLM client directly, so reusing it guarantees behavioural
    consistency between P1 sub-agent execution and P2 parallel branches and
    avoids a second LLM-call code path.

    The branches fan out via :class:`ParallelNode` (each on a cloned state),
    then a :class:`FanInNode` (``merge_mode="concat"``) merges all branch
    outputs into ``state.output``. An optional terminal ``synthesizer``
    :class:`FunctionNode` further condenses the concatenated perspectives; it
    too routes through ``run_agent_turn`` (using the *first* agent as the
    synthesizer), keeping the single LLM-call-path invariant.

    Red lines honoured here:
        R1 — no ``memory_event_bus.emit`` / ``_trigger_*`` anywhere in this
             function body (grep clean). Memory writes happen fan-in, on the
             main agent — never on parallel branches.
        R2 — ``_build_execution_graph`` / ``/execute`` are untouched.
    """
    graph = StateGraph("parallel-graph")

    # ── One branch per agent_id, each delegating to run_agent_turn ──────
    # Closure captures the per-branch agent_id so each FunctionNode handler
    # calls run_agent_turn for its own agent. The handler sets branch output
    # so FanInNode(concat) can collect it from parallel_results.
    def _make_branch_handler(agent_id: str):
        async def _handler(state: GraphState) -> GraphState:
            response = await run_agent_turn(
                agent_id=agent_id,
                input=input,
                session_id=session_id,
            )
            state.output = response or ""
            state.current_node = f"llm_{agent_id}"
            return state

        return _handler

    branches: dict[str, list[GraphNode]] = {
        f"branch_{aid}": [FunctionNode(f"llm_{aid}", _make_branch_handler(aid))]
        for aid in agent_ids
    }

    # entry → parallel fan-out → fan-in (concat) → synthesizer (optional) → end
    graph.add_node(
        "multi_perspective",
        ParallelNode("multi_perspective", branches),
    )
    graph.add_node(
        "fan_in",
        FanInNode("fan_in", source_name="multi_perspective", merge_mode="concat"),
    )

    graph.add_edge("multi_perspective", "fan_in")

    # Optional synthesizer: condense the concatenated perspectives using the
    # first agent (keeps the single run_agent_turn LLM-call-path invariant).
    if agent_ids:
        synth_agent = agent_ids[0]

        async def _synth_handler(state: GraphState) -> GraphState:
            perspectives = state.output or ""
            synth_input = (
                "Synthesize the following multiple perspectives into a single "
                f"unified answer:\n\n{perspectives}"
            )
            response = await run_agent_turn(
                agent_id=synth_agent,
                input=synth_input,
                session_id=session_id,
            )
            state.output = response or perspectives
            state.current_node = "synthesizer"
            return state

        graph.add_node("synthesizer", FunctionNode("synthesizer", _synth_handler))
        graph.add_edge("fan_in", "synthesizer")
        graph.set_entry_point("multi_perspective")
    else:
        graph.set_entry_point("multi_perspective")

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
    await _state.fire(
        EventType.TURN_END,
        TurnContext(agent_id=agent_id, session_id=session_id, conversation_item=conversation_item),
    )

    # session→episodic migration, fire-and-forget (was: _migrate_and_emit)
    _fire_write(
        agent_id,
        lambda: _state.fire(
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
    """Execute a native pydantic-ai Agent (P8: 自研 graph tool 循环 → Agent.run)。

    ObserveCapability/MemoryWriterCapability/ToolBridgeCapability 在 Agent.run 内横切
    (tick 闭环 + 记忆沉淀 + tool dispatch)。SSE 简化为 agent_status/execution_complete/
    error(web 弃用后 node SSE 无消费者)。canvas 随 web 弃用退役。
    """
    agent = _state.agents.get(req.agent_id)
    if not agent:
        return JSONResponse({"error": "Agent not found"}, status_code=404)

    session_id = req.session_id or str(uuid.uuid4())
    # ADR-2 H2:TURN_SUBMIT 最早 fire(用户 prompt 入口,pre-execution)。
    await _state.fire(
        EventType.TURN_SUBMIT,
        TurnContext(agent_id=req.agent_id, session_id=session_id),
    )
    # 通信桥:把执行 agent 注册进 session,使 broadcast 收件人非空。
    _state.communication_bus.register_agent(req.agent_id, session_id)
    await _state.fire(
        EventType.SESSION_START,
        SessionContext(agent_id=req.agent_id, session_id=session_id),
    )

    # native Agent:全 capability 横切 + AnthropicModel cache(R2 替代 ContextCompiler/static_count)
    from src.harness.native_agent import build_native_agent, run_agent_turn_with_stop
    from src.harness.capabilities import (
        GuardrailCapability, MemoryWriterCapability, ObserveCapability, ToolBridgeCapability,
    )
    from src.harness.emit import ObserveEmitter
    from src.tools.guardrail import Guardrail

    harness_id = f"exec_{session_id[:8]}"
    emitter = ObserveEmitter("agent-os-v2", harness_id=harness_id, session_id=session_id)
    native_agent = build_native_agent(
        instructions=agent.get("system_prompt") or "You are a helpful assistant.",
        capabilities=[
            ObserveCapability(emitter=emitter, harness_id=harness_id, session_id=session_id),
            MemoryWriterCapability(
                memory_event_bus=_state.memory_event_bus,
                knowledge_graph=_state.knowledge_graph,
                agent_id=req.agent_id, session_id=session_id,
            ),
            ToolBridgeCapability(
                tool_executor=_state.tool_executor, pitfail_registry=_state.pitfail_registry,
            ),
            GuardrailCapability(guardrail=Guardrail()),
        ],
        model_settings={
            "anthropic_cache_instructions": "5m",      # R2: system prompt cache
            "anthropic_cache_tool_definitions": "5m",  # R2: tool schema cache
        },
    )

    async def event_stream() -> AsyncGenerator[str, None]:
        await _state.concurrency_controller.acquire_agent_slot(req.agent_id)
        agent["status"] = "running"
        yield _sse("agent_status", {"agent_id": req.agent_id, "status": "running"})
        try:
            try:
                await emitter.connect()  # best-effort(observe 断不影响 run,ADR-7)
            except Exception:
                logger.warning("execute emitter connect failed (%s)", harness_id)
            # ADR-2 H2:TURN_START(agent.run 前;reserved 接 fire,recall 注入点保留)。
            await _state.fire(
                EventType.TURN_START,
                TurnContext(agent_id=req.agent_id, session_id=session_id),
            )
            result = await run_agent_turn_with_stop(  # fire STOP(完成,native_agent.py)
                native_agent, req.input,
                session_id=session_id, agent_id=req.agent_id,
                bus=_state.memory_event_bus,
            )
            agent["status"] = "idle"
            yield _sse("agent_status", {"agent_id": req.agent_id, "status": "idle"})
            yield _sse("execution_complete", {
                "output": result.output,
                "session_id": session_id,
                "memory_count": 0,  # MemoryWriter 内部沉淀,无 memory_refs 计数
            })
            # 对话历史持久化(供 /v1/conversations 列表 + 回看)。
            if _state.conversation_registry is not None:
                try:
                    _state.conversation_registry.record_turn(
                        session_id=session_id, agent_id=req.agent_id,
                        user_input=req.input, assistant_response=result.output or "",
                    )
                except Exception:
                    logger.warning("conversation record_turn failed", exc_info=True)
            # Part5: side-agent task-experience consolidation removed (archived).
        except Exception as exc:
            agent["status"] = "idle"
            yield _sse("error", {"message": str(exc)})
        finally:
            try:
                await emitter.close()
            except Exception:
                pass
            await _state.concurrency_controller.release_agent_slot(req.agent_id)
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ── Execute Parallel (SSE, P2) ─────────────────────────────────────


@router.post("/execute_parallel")
async def execute_parallel(req: ExecuteParallelRequest) -> StreamingResponse:
    """Run a multi-perspective parallel graph and stream node status via SSE.

    Fans out one branch per ``agent_ids`` entry (each calling
    :func:`run_agent_turn` — the P1 primitive, R1-compliant), fans in the
    branch outputs (``concat``), optionally synthesizes, and streams the
    per-node progress. Physically independent of ``/execute`` (R2): a
    separate graph builder, separate request model (R3), no memory hooks on
    the parallel branches.
    """
    # Validate that every requested agent exists in the registry.
    missing = [aid for aid in req.agent_ids if aid not in _state.agents]
    if missing:
        return JSONResponse(
            {"error": "Agent(s) not found", "missing": missing}, status_code=404,
        )
    if not req.agent_ids:
        return JSONResponse(
            {"error": "agent_ids must be non-empty"}, status_code=400,
        )

    session_id = req.session_id or str(uuid.uuid4())

    # Register every branch agent into the session so communication-bus
    # broadcast recipients are non-empty (mirrors /execute's single-agent
    # register_agent call, scaled to N branches).
    for aid in req.agent_ids:
        _state.communication_bus.register_agent(aid, session_id)

    graph = _build_parallel_graph(req.agent_ids, req.input, session_id)
    initial_state = GraphState(
        input=req.input,
        agent_id=req.agent_ids[0],
        session_id=session_id,
    )

    event_queue: asyncio.Queue[str | None] = asyncio.Queue()

    async def on_node_complete(node_name: str, state: GraphState) -> None:
        # ParallelNode/FanInNode/synthesizer — emit SSE so the fan-out/fan-in
        # stages are visible to the client. Branch-level (llm_<aid>) nodes
        # run *inside* ParallelNode on cloned states and are not surfaced as
        # top-level graph nodes (only the ParallelNode wrapper is a graph
        # node), so we key on the wrapper names.
        try:
            msg = AgentMessage(
                sender_id=req.agent_ids[0],
                recipient_id=None,
                session_id=session_id,
                content=f"Node {node_name} completed: {state.output[:100] if state.output else ''}",
                message_type=MessageType.NOTIFICATION,
            )
            await _state.communication_bus.broadcast(msg, session_id=session_id)
        except Exception:
            pass

        if node_name == "multi_perspective":
            branch_outputs = state.parallel_results.get(node_name, [])
            await event_queue.put(_sse("node_start", {
                "node": "multi_perspective", "status": "running",
                "branches": [b.get("branch") for b in branch_outputs],
            }))
            await event_queue.put(_sse("node_complete", {
                "node": "multi_perspective", "status": "done",
                "branch_count": len(branch_outputs),
                "branches": [
                    {"branch": b.get("branch"), "output": b.get("output", "")}
                    for b in branch_outputs
                ],
            }))
        elif node_name == "fan_in":
            await event_queue.put(_sse("node_start", {"node": "fan_in", "status": "running"}))
            await event_queue.put(_sse("node_complete", {
                "node": "fan_in", "status": "done", "output": state.output,
            }))
        elif node_name == "synthesizer":
            await event_queue.put(_sse("node_start", {"node": "synthesizer", "status": "running"}))
            await event_queue.put(_sse("node_complete", {
                "node": "synthesizer", "status": "done", "output": state.output,
            }))

    async def event_stream() -> AsyncGenerator[str, None]:
        # Acquire a slot for the (primary) agent so the global concurrency
        # controller is respected. Parallel branches run on their own cloned
        # states and do not contend for slots (they are transient sub-agents).
        await _state.concurrency_controller.acquire_agent_slot(req.agent_ids[0])
        yield _sse("agent_status", {
            "agent_id": req.agent_ids[0], "status": "running",
            "parallel": True,
        })

        async def run_graph():
            try:
                final_state = await graph.run(
                    initial_state, on_node_complete=on_node_complete,
                )
                await event_queue.put(_sse("agent_status", {
                    "agent_id": req.agent_ids[0], "status": "idle",
                }))
                await event_queue.put(_sse("execution_complete", {
                    "output": final_state.output,
                    "session_id": final_state.session_id,
                    "branches": req.agent_ids,
                    "parallel_results": final_state.parallel_results.get(
                        "multi_perspective", [],
                    ),
                }))
            except Exception as exc:
                await event_queue.put(_sse("error", {"message": str(exc)}))
            finally:
                await _state.concurrency_controller.release_agent_slot(
                    req.agent_ids[0],
                )
                await event_queue.put(None)

        task = asyncio.create_task(run_graph())

        while True:
            item = await event_queue.get()
            if item is None:
                break
            yield item

        yield "data: [DONE]\n\n"
        await task

    return StreamingResponse(event_stream(), media_type="text/event-stream")
