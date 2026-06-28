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

红线
----
R1(记忆零触碰):
    - 分支(AgentWorkerNode)经 run_agent_turn,后者已剥离记忆(P1)。
    - synthesizer 经 run_agent_turn(orchestrator_id)—— orchestrator 是真实
      持久 agent,记忆在其自身执行图(若挂)单点落库。本函数体零 memory 调用。
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

import logging
from typing import Any

from src.agent.meta.agent_runner import run_agent_turn
from src.graph import FanInNode, ParallelNode, StateGraph
from src.graph.nodes import FunctionNode, GraphNode
from src.graph.state import GraphState
from src.orchestration.agent_worker_node import AgentWorkerNode

logger = logging.getLogger(__name__)


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
             function body (grep clean). Branch subagents carry no memory;
             the orchestrator's synthesizer turn is the single memory point.
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
    async def _synth_handler(state: GraphState) -> GraphState:
        perspectives = state.output or ""
        synth_input = (
            "You are the orchestrator. Synthesize the following sub-agent "
            "perspectives into a single unified answer:\n\n"
            f"{perspectives}"
        )
        response = await run_agent_turn(
            agent_id=orchestrator_id,
            input=synth_input,
            session_id=session_id,
        )
        state.output = response or perspectives
        state.current_node = "synthesizer"
        return state

    graph.add_node("synthesizer", FunctionNode("synthesizer", _synth_handler))
    graph.add_edge("fan_in", "synthesizer")

    graph.set_entry_point("multi_agent")
    return graph
