"""Chat and Execute SSE routes — includes graph execution pipeline."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re as _re
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


# ── SSE helpers ────────────────────────────────────────────────────


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


# ── Tool invocation detection ─────────────────────────────────────

_TOOL_INVOCATION_RE = _re.compile(
    r'\b(?:tool_call|function_call|action)\s*[:=]\s*["\']?(\w+)',
    _re.IGNORECASE,
)


def _has_tool_invocation(text: str) -> bool:
    return bool(_TOOL_INVOCATION_RE.search(text))


# Multi-turn tool_use loop: hard ceiling on how many times the model may chain
# tool calls before the ``llm`` conditional edge forces ``llm_synthesize``.
# Guards against a tool-happy model looping forever (e.g. always re-emitting a
# tool_use). Overridable via env for tuning.
MAX_TOOL_ITERATIONS = int(os.environ.get("MAX_TOOL_ITERATIONS", "5"))


def _inject_tool_history(
    messages: list[dict[str, Any]],
    history: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Append the accumulated tool_use/tool_result pairs to *messages*.

    Replays each recorded round as the canonical anthropic tool-use turn
    structure so the model observes every prior tool call and its result
    before deciding the next step::

        assistant: [{"type": "tool_use", "id", "name", "input"}]
        user:      [{"type": "tool_result", "tool_use_id", "content"}]

    The caller has already appended the user input once (first round); this
    only adds the tool turns, so the resulting message list is a valid
    alternating role sequence for the Anthropic channel.
    """
    out = list(messages)
    for entry in history:
        tu = entry.get("tool_use") or {}
        tr = entry.get("tool_result", "")
        tu_id = tu.get("id", "")
        out.append(
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": tu_id,
                        "name": tu.get("name", ""),
                        "input": tu.get("input", {}) or {},
                    }
                ],
            }
        )
        out.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": tu_id,
                        "content": tr,
                    }
                ],
            }
        )
    return out


def _build_native_tools() -> list[dict[str, Any]]:
    """Convert the ToolRegistry listing to Anthropic-native tool schemas.

    The registry stores each tool as ``{name, description, parameters}`` where
    ``parameters`` is already a JSON Schema (type/properties/required) — exactly
    what Anthropic expects under ``input_schema``. This is a thin rename so the
    orchestrator can pass tools verbatim to ``LLMClient.chat(tools=...)`` and
    get native ``tool_use`` back instead of fragile regex parsing.

    Returns ``[]`` (empty → caller treats as "no tools") when the registry is
    not initialized or is empty.
    """
    if _state.tool_executor is None or _state.tool_executor.registry is None:
        return []
    tools: list[dict[str, Any]] = []
    for t in _state.tool_executor.registry.list_tools():
        tools.append(
            {
                "name": t["name"],
                "description": t.get("description", ""),
                "input_schema": t.get("parameters") or {"type": "object", "properties": {}},
            }
        )
    return tools


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
    """LLM processing with real API call and memory integration.

    In the multi-turn tool_use loop this node is entered once after ``start``
    (first round) and again after every ``tool`` round (``tool → llm``). Each
    entry the model sees the *accumulated* tool history (anthropic
    ``tool_use``/``tool_result`` content-block sequence) so it can decide
    whether another tool call is needed or the request is answered.
    """
    agent_id = state.agent_id
    session_id = state.session_id
    user_input = state.input

    agent = _state.agents.get(agent_id)
    agent_model = agent.get("model") if agent else None

    # First round only: append the user input to the persistent message
    # history. On subsequent rounds the user turn is already there and the
    # tool loop replays as assistant tool_use + user tool_result turns (see
    # the history injection below). Without this guard the user input would
    # be duplicated once per loop iteration.
    first_round = state.tool_iteration == 0
    conversation = list(state.messages)
    if first_round:
        conversation.append({"role": "user", "content": user_input})

    # Multi-turn tool_result回注: replay the accumulated tool_use/tool_result
    # history as an anthropic content-block sequence so the model observes
    # prior tool calls and their results before deciding the next step. This
    # mirrors the canonical anthropic tool-use turn structure:
    #   assistant: [tool_use]
    #   user:      [tool_result]
    # Only injected when there is history (first round has none).
    if state.tool_use_history:
        conversation = _inject_tool_history(conversation, state.tool_use_history)

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
        # Native function-calling: build Anthropic-shaped tool schemas from
        # the registry and pass them to the LLM. The model decides whether to
        # call a tool; a tool_use block surfaces on ``last_tool_use`` when it
        # does. ``_build_native_tools`` returns [] when no registry/tools.
        native_tools = _build_native_tools()
        response = await _state.llm_client.chat(
            llm_messages,
            model=agent_model,
            static_count=compiled.static_count,
            tools=native_tools or None,
        )
    except LLMError as exc:
        state.errors.append(f"LLM error: {exc}")
        state.output = f"[LLM unavailable] {exc}"
        state.current_node = "llm"
        _state.log_execution_step("llm", state, status="error")
        return state

    # Persist the user turn exactly once; persist the assistant reply every
    # round (each loop iteration produces a fresh assistant message). This
    # keeps state.messages a faithful transcript for synthesis + memory hooks.
    if first_round:
        state.messages.append({"role": "user", "content": user_input})
    state.messages.append({"role": "assistant", "content": response})
    state.output = response
    state.current_node = "llm"

    # Native tool_use is the primary path (model emitted a tool_use block).
    # IMPORTANT: clear any stale tool_call from a previous round first, then
    # set it only when the model actually emitted a new tool_use this round.
    # Without the clear, a prior round's tool_call would linger and keep
    # ``needs_tool`` True forever, looping the graph until MAX_TOOL_ITERATIONS.
    tool_use = getattr(_state.llm_client, "last_tool_use", None)
    state.context.pop("tool_call", None)
    state.context.pop("tool_args", None)
    if tool_use and tool_use.get("name"):
        state.context["tool_call"] = tool_use["name"]
        state.context["tool_args"] = tool_use.get("input", {}) or {}

    # Memory hooks run only on the first round to avoid re-sedimenting the
    # same user turn / re-firing KG extraction / re-compressing on every loop
    # iteration. Subsequent rounds are tool-driven continuations; their tool
    # results are sedimented by ``_node_tool``.
    if first_round:
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


def _classify_tool_error(error_msg: str) -> str:
    """粗粒度工具错误分类(供 PitFail error_type 维度)。

    从 error_msg 关键词推断 timeout / file_not_found / permission_denied,否则
    归为通用 tool_error。match(tool_name, error_type) 依赖稳定 error_type,故分
    类规则保持简单确定性(无模糊启发式)。
    """
    _msg = (error_msg or "").lower()
    if "timeout" in _msg or "timed out" in _msg:
        return "timeout"
    if "not found" in _msg or "no such file" in _msg or "filenotfound" in _msg:
        return "file_not_found"
    if "permission" in _msg or "denied" in _msg:
        return "permission_denied"
    return "tool_error"


async def _node_tool(state: GraphState) -> GraphState:
    """Execute a tool call via ToolExecutor."""
    # Default to a *registered* tool whose signature matches the args. The old
    # defaults (``web_search`` + ``{"query": ...}``) were doubly broken:
    # ``web_search`` is not in the registry so the executor always returned
    # "not found", and ``{"query": ...}`` did not match any handler signature
    # (http_get(url,...) / file_read(path,...)). ``file_read`` is registered by
    # engine.py and degrades gracefully (File not found) for arbitrary input,
    # so a mis-routed tool call no longer forces the error branch.
    tool_name = state.context.get("tool_call", "file_read")
    tool_args = state.context.get("tool_args", {"path": state.input})

    result = await _state.tool_executor.execute(tool_name, tool_args)

    if result["status"] != "success":
        error_msg = result.get("error", "Unknown error")
        state.context["tool_result"] = f"[Tool error] {tool_name}: {error_msg}"
        # PitFail 通电:工具失败 → 复发计数(match 命中)或新记录(record)。
        # 全程 try/except 包裹 —— pitfail 任何异常都不影响主工具流程(零回归)。
        if _state.pitfail_registry is not None:
            try:
                _pf_err_type = _classify_tool_error(error_msg)
                _existing = _state.pitfail_registry.match(tool_name, _pf_err_type)
                if _existing:
                    _state.pitfail_registry.increment_recurrence(_existing[0].id)
                else:
                    from src.pitfail import PitfallRecord
                    _state.pitfail_registry.record(PitfallRecord(
                        id="", file_path=tool_name, error_type=_pf_err_type,
                        symptom=error_msg, root_cause=error_msg, fix="",
                        tags=[tool_name],
                    ))
            except Exception:
                # pitfail 记录失败绝不能阻断主工具执行 —— 静默降级。
                pass
    else:
        state.context["tool_result"] = str(result["output"])

    state.tool_results.append({"tool": tool_name, "result": state.context["tool_result"]})

    # Multi-turn loop: record the {tool_use, tool_result} pair so the next
    # ``_node_llm`` round can replay the full anthropic tool_use/tool_result
    # sequence into messages (model sees prior results before deciding).
    tool_use_block = {
        "type": "tool_use",
        "id": f"toolu_iter{state.tool_iteration}",
        "name": tool_name,
        "input": dict(tool_args),
    }
    state.tool_use_history.append(
        {"tool_use": tool_use_block, "tool_result": state.context["tool_result"]}
    )

    # Anti-infinite-loop: count this round. The ``llm`` conditional edge reads
    # ``tool_iteration`` against MAX_TOOL_ITERATIONS to force synthesis.
    state.tool_iteration += 1

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
    """LLM synthesizes the *full* tool history into a final answer.

    Reached either after a single tool round (no further tool requested) or
    when the multi-turn loop exhausts ``MAX_TOOL_ITERATIONS``. Reads every
    recorded ``tool_use_history`` entry so the synthesized answer can draw on
    all prior tool calls (not just the most recent result).
    """
    # Build a readable transcript of every tool round for the synthesis prompt.
    if state.tool_use_history:
        tool_lines = []
        for i, entry in enumerate(state.tool_use_history, start=1):
            tu = entry.get("tool_use") or {}
            name = tu.get("name", "?")
            args = tu.get("input", {}) or {}
            res = entry.get("tool_result", "")
            tool_lines.append(f"[{i}] {name}({args}) → {res}")
        tool_result_block = "\n".join(tool_lines)
    else:
        # Fallback to the legacy single-result context field (covers any path
        # that set tool_result without going through the loop history).
        tool_result_block = state.context.get("tool_result", "")

    agent = _state.agents.get(state.agent_id)
    agent_model = agent.get("model") if agent else None
    system_prompt = (agent.get("system_prompt") if agent else None) or "You are a helpful assistant."

    if _state.context_compiler is not None:
        conversation = list(state.messages) + [
            {"role": "user", "content": state.input},
            {"role": "system", "content": f"Tool results:\n{tool_result_block}"},
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
            {"role": "user", "content": f"Original question: {state.input}\n\nTool results:\n{tool_result_block}"},
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
    """Build the orchestration graph with nodes and edges.

    Multi-turn tool_use loop (P1)::

        start ──▶ llm ──(needs_tool AND tool_iteration < MAX?)──▶ tool ──▶ llm ──▶ …
                    │                                              (loop back)
                    └──(else)──▶ llm_synthesize ──▶ (end)

    The ``tool`` node loops back to ``llm`` (not ``llm_synthesize``) so the
    model reads the tool_result and decides whether another tool call is
    needed. The ``llm`` conditional edge routes to ``tool`` only while the
    model still wants a tool AND the iteration budget remains; otherwise it
    routes to ``llm_synthesize`` for a final answer. ``llm_synthesize`` is a
    terminal node (no outgoing edge) so the graph ends there.
    """
    graph = StateGraph("exec-graph")
    graph.set_checkpoint_store(InMemoryCheckpointStore())

    graph.add_node("start", FunctionNode("start", _node_start))
    graph.add_node("llm", FunctionNode("llm", _node_llm))
    graph.add_node("tool", FunctionNode("tool", _node_tool))
    graph.add_node("llm_synthesize", FunctionNode("llm_synthesize", _node_llm_synthesize))

    graph.add_edge("start", "llm")
    # Loop back: tool → llm so the model reads tool_result and decides next.
    # (Was: tool → llm_synthesize, which only ever allowed a single round.)
    graph.add_edge("tool", "llm")

    def _llm_route(state: GraphState) -> str:
        # Stop chaining when the model no longer requests a tool, OR when the
        # iteration budget is exhausted (force a synthesized answer rather
        # than loop forever).
        if state.context.get("needs_tool") and state.tool_iteration < MAX_TOOL_ITERATIONS:
            return "tool"
        return "llm_synthesize"

    graph.add_conditional_edge(
        source="llm",
        targets={
            "tool": "tool",
            "llm_synthesize": "llm_synthesize",
        },
        condition=_llm_route,
    )

    graph.set_entry_point("start")
    return graph


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
    # 通信桥:把执行 agent 注册进 session,使 broadcast 收件人非空。否则
    # on_node_complete 的 communication_bus.broadcast 在空 session 下投递数为 0
    # (broadcast 跳过 sender,且 _session_members 里没有该 session 的成员)。
    _state.communication_bus.register_agent(req.agent_id, session_id)
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
