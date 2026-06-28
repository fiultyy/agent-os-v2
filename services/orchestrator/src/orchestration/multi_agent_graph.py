"""_build_multi_agent_graph — P3 多 agent 编排生产图构造器。

拓扑::

    entry(multi_agent ParallelNode)
        ├─ branch_<role_a>: [AgentWorkerNode(role_a, ...)]
        ├─ branch_<role_b>: [AgentWorkerNode(role_b, ...)]
        └─ …
        ──▶ fan_in(FanInNode, merge_mode="aggregate")
        ──▶ synthesizer(FunctionNode, 复用 run_agent_turn(orchestrator_id) 综合)

区别于 P2 的 :func:`_build_parallel_graph`(分支是 *预存* agent 的
``run_agent_turn``),这里每个分支是一个 :class:`AgentWorkerNode`,它 *真实
spawn* 一个临时 subagent(create_subagent)、跑一轮、然后 teardown —— 完整
生命周期自洽。

synthesizer 复用 :func:`run_agent_turn` 调 **orchestrator**(主 agent)做综合,
这是 R1 的"记忆单点落库"出口:fan-in 之后只有 orchestrator 这一个真实 agent
跑 LLM,记忆(若挂)只在此点触发;分支 subagent 全程零 memory。

ADR-3(编排记忆沉淀)::

    synthesizer 综合轮返回 response 后,orchestrator 单点 emit
    TURN_END/SESSION_END + KG extract(对称于 ``/execute`` 单 agent 每轮落库),
    让编排闭环也沉淀记忆。emit 复用既有 ``memory_event_bus`` 入口(与
    ``chat.py`` 的 ``/chat`` + ``_node_llm`` 同模式),评分内部逻辑零改动。
    env gate:``memory_event_bus`` / ``knowledge_graph`` 未 wired → no-op(try/except
    降级不崩,与 ``/execute`` 降级同)。

红线
----
R1(记忆零触碰):
    - 分支(AgentWorkerNode)经 run_agent_turn,后者已剥离记忆(P1),
      **全程零 memory emit**(grep clean,测试按 agent_id 过滤断言)。
    - synthesizer 经 run_agent_turn(orchestrator_id)—— orchestrator 是真实
      持久 agent,记忆单点落库只在 synthesizer 综合轮后的
      ``_emit_orchestrator_synthesis_memory`` helper 里(模块级,本函数体零直接
      ``memory_event_bus.emit`` 调用)。
R2(主路径冻结):
    本函数 *不 import 也不修改* ``_build_execution_graph`` / ``/execute`` /
    ``_build_parallel_graph`` —— 物理隔离,纯新文件。

复用
----
- :class:`src.graph.ParallelNode` / :class:`src.graph.FanInNode`
- :class:`src.graph.nodes.FunctionNode`
- :func:`src.agent.meta.agent_runner.run_agent_turn` (P1 原语)
- :class:`src.orchestration.agent_worker_node.AgentWorkerNode` (P3 worker)
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from src.agent.meta.agent_runner import run_agent_turn
from src.graph import FanInNode, ParallelNode, StateGraph
from src.graph.nodes import FunctionNode, GraphNode
from src.graph.state import GraphState
from src.orchestration.agent_worker_node import AgentWorkerNode
from src.memory import MemoryScope, MemoryType
from src.memory.event_bus import EventType
from src.memory.hooks import SessionContext, TurnContext
from src.memory.types import MemoryItem
from src.services import _state

logger = logging.getLogger(__name__)


# ── ADR-3:orchestrator fan-in 单点记忆沉淀 helper ──────────────────────
# 对称于 ``chat.py`` 的 ``/chat`` + ``_node_llm`` 每轮落库模式:TURN_END 存 working
# 记忆 → fire-and-forget SESSION_END(会话→情景迁移)→ fire-and-forget KG extract。
# 复用既有 ``memory_event_bus`` emit 入口(评分内部逻辑零改动)。env gate:
# ``memory_event_bus`` / ``knowledge_graph`` 未 wired → no-op(try/except 降级不崩,
# 与 ``/execute`` 降级同)。本 helper 只被 synthesizer(orchestrator)单点调用 ——
# 分支(AgentWorkerNode → run_agent_turn)零记忆,守恒 R1。


def _emit_orchestrator_synthesis_memory(
    orchestrator_id: str,
    session_id: str,
    user_input: str,
    assistant_response: str,
) -> None:
    """Orchestrator synthesizer 单点记忆沉淀(ADR-3)。

    TURN_END(working 记忆 → core store)→ SESSION_END fire-and-forget(会话→
    情景迁移)→ KG extract fire-and-forget。注:与 /execute 的 INGEST+KG 段对称
    (fire-and-forget);TURN_END 段 /execute 用 await 而本 helper 用 fire-and-forget
    (综合轮不阻塞响应);不发 INGEST(默认 MEMORY_INGESTOR_ENABLED=0 时 /execute 亦 no-op)。

    env gate:``_state.memory_event_bus`` / ``_state.knowledge_graph`` 任一为 None
    → 对应 emit 静默 no-op(try/except 降级,绝不 raise,综合响应仍返回)。
    不触碰五维/召回/蝴蝶翼/ExperienceKG 写侧评分 —— 全走既有 hook 入口。
    """
    # ① TURN_END:把综合轮存成 working 记忆(core store)。
    try:
        bus = _state.memory_event_bus
        if bus is not None:
            working_item = MemoryItem(
                content=f"User: {user_input}\nAssistant: {assistant_response}",
                agent_id=orchestrator_id,
                session_id=session_id,
                memory_type=MemoryType.WORKING,
                scope=MemoryScope.AGENT,
            )
            asyncio.create_task(
                bus.emit(
                    EventType.TURN_END,
                    TurnContext(
                        agent_id=orchestrator_id,
                        session_id=session_id,
                        working_item=working_item,
                    ),
                )
            )
            # ② SESSION_END:fire-and-forget 会话→情景迁移(对称 /chat)。
            asyncio.create_task(
                bus.emit(
                    EventType.SESSION_END,
                    SessionContext(agent_id=orchestrator_id, session_id=session_id),
                )
            )
    except Exception:
        logger.warning(
            "orchestrator TURN_END/SESSION_END emit failed (env gate no-op)",
            exc_info=True,
        )

    # ③ KG extract:fire-and-forget 实体/关系抽取(对称 /chat + /execute)。
    try:
        kg = _state.knowledge_graph
        if kg is not None and hasattr(kg, "extract_and_ingest"):
            text = f"User: {user_input}\nAssistant: {assistant_response}"
            asyncio.create_task(
                asyncio.to_thread(
                    kg.extract_and_ingest, text=text, memory_id=session_id,
                )
            )
    except Exception:
        logger.warning("orchestrator KG extraction failed (env gate no-op)", exc_info=True)


def _build_multi_agent_graph(
    orchestrator_id: str,
    sub_agents_spec: list[dict[str, Any]],
    input: str,
    session_id: str,
    agent_manager: Any,
) -> StateGraph:
    """Build the P3 multi-agent orchestration production graph.

    Args:
        orchestrator_id: 主 orchestrator agent_id(综合用,亦作 worker 的
            ``parent_id`` 溯源)。必须是 ``_state.agents`` 里的真实 agent。
        sub_agents_spec: subagent 角色规格列表,每项形如::

                {
                    "role": "researcher",            # 必填,作 agent_type
                    "config": {                       # 可选
                        "name": "...", "model": "...",
                        "system_prompt": "...", "tools": [...],
                    },
                    "input": "...",                   # 可选,覆盖顶层 input
                    "system_prompt": "...",           # 可选,覆盖 config
                }

        input: 顶层任务输入;未单独指定 ``input`` 的 worker 用它。
        session_id: 会话 id,贯穿 create_subagent / run_agent_turn。
        agent_manager: 提供 ``create_subagent`` / ``teardown_subagent`` 的管理器
            (P0 契约),注入每个 AgentWorkerNode。

    Returns:
        配置好的 :class:`StateGraph`,entry = ``"multi_agent"`` ParallelNode,
        末端 = ``"synthesizer"`` FunctionNode(综合 orchestrator)。

    Red lines honoured here:
        R1 — no ``memory_event_bus.emit`` / ``_trigger_*`` anywhere in this
             function body (AST/grep clean); branch subagents carry no memory.
             The orchestrator's synthesizer turn is the single memory point,
             sedimented by the module-level
             :func:`_emit_orchestrator_synthesis_memory` helper called from
             ``_synth_handler`` (ADR-3).
        R2 — ``_build_execution_graph`` / ``/execute`` / ``_build_parallel_graph``
             are untouched (物理隔离).
    """
    graph = StateGraph("multi-agent-orchestration")

    # ── 一个 role 一个 AgentWorkerNode 分支 ─────────────────────────
    # 每个 worker 自带 spawn→run→teardown 闭环;ParallelNode 在克隆 state 上
    # 并发跑各分支,FanInNode(aggregate)汇聚到 state.output。
    branches: dict[str, list[GraphNode]] = {}
    for spec in sub_agents_spec:
        role = spec.get("role") or spec.get("name") or "subagent"
        branch_name = f"branch_{role}"
        worker = AgentWorkerNode(
            name=branch_name,
            role=role,
            agent_manager=agent_manager,
            config=spec.get("config", {}),
            orchestrator_id=orchestrator_id,
            session_id=session_id,
            turn_input=spec.get("input"),
            system_prompt=spec.get("system_prompt"),
        )
        branches[branch_name] = [worker]

    graph.add_node(
        "multi_agent",
        ParallelNode("multi_agent", branches),
    )
    graph.add_node(
        "fan_in",
        FanInNode("fan_in", source_name="multi_agent", merge_mode="aggregate"),
    )
    graph.add_edge("multi_agent", "fan_in")

    # ── synthesizer:复用 run_agent_turn(orchestrator_id) 综合 ─────────
    # R1 出口:fan-in 之后只有 orchestrator(主 agent)跑 LLM。orchestrator 是
    # 真实持久 agent,记忆(若挂)只在此点单点落库;分支 subagent 全程零 memory。
    # ADR-3:synthesizer 综合轮返回后,orchestrator 单点 emit 记忆(TURN_END/
    # SESSION_END + KG,复用既有 memory_event_bus 入口,对称 /execute 每轮落库)。
    # 分支经 AgentWorkerNode → run_agent_turn 零记忆不变(run_agent_turn 不改)。
    async def _synth_handler(state: GraphState) -> GraphState:
        perspectives = state.output or ""
        # ADR-3 闭环最后一块:综合轮前召回 orchestrator 自己的历史记忆(只读
        # retrieve,不改排序 match×lif/五维/蝴蝶翼/origin)注入 synth_input ——
        # 编排真正闭环(沉淀→召回→注入综合)。env gate:_state.retriever 未 wired
        # (None)→ no-op,synth_input 不含历史,走原 perspectives 逻辑,绝不崩。
        # R1:分支(AgentWorkerNode → run_agent_turn)零记忆不变,只 orchestrator
        # 综合轮单点召回(只读)。run_agent_turn 不改。
        memory_block = ""
        retriever = _state.retriever
        if retriever is not None:
            try:
                recall_results = await retriever.retrieve(
                    query=perspectives or "",
                    agent_id=orchestrator_id,
                    top_k=5,
                )
            except Exception:
                logger.warning(
                    "orchestrator self-recall failed (env gate no-op)",
                    exc_info=True,
                )
                recall_results = []
            if recall_results:
                recall_lines: list[str] = []
                for r in recall_results:
                    item = r.get("item") if isinstance(r, dict) else None
                    if item is None:
                        continue
                    content = getattr(item, "content", "") or ""
                    if content:
                        recall_lines.append(f"- {content}")
                if recall_lines:
                    memory_block = (
                        "相关历史记忆:\n"
                        + "\n".join(recall_lines)
                        + "\n\n"
                    )
        synth_input = (
            "You are the orchestrator. Synthesize the following sub-agent "
            "perspectives into a single unified answer:\n\n"
            f"{memory_block}{perspectives}"
        )
        response = await run_agent_turn(
            agent_id=orchestrator_id,
            input=synth_input,
            session_id=session_id,
        )
        # ADR-3:orchestrator 单点记忆沉淀(env gate:memory 未 wired → no-op)。
        _emit_orchestrator_synthesis_memory(
            orchestrator_id=orchestrator_id,
            session_id=session_id,
            user_input=input,  # P2(kg审核):沉淀用原始 input(非含召回块的 synth_input),避免递归重摄入记忆膨胀
            assistant_response=response or perspectives,
        )
        state.output = response or perspectives
        state.current_node = "synthesizer"
        return state

    graph.add_node("synthesizer", FunctionNode("synthesizer", _synth_handler))
    graph.add_edge("fan_in", "synthesizer")

    graph.set_entry_point("multi_agent")
    return graph
