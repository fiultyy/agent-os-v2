"""graph loop ↔ pydantic-ai 2 桥接节点单测(ADR「graph 聚焦编排器」结合点)。

证明封装正确:agent_turn_node 跑 Agent.run → output + messages 回写;续聊 history 从
GraphState.context 取;多 agent 拓扑(两节点链 / ParallelNode+FanIn)graph.run 编排通。

TestModel 哑模型,不真调 glm。agent_factory 注入测试 Agent。
"""

from __future__ import annotations

import asyncio
from typing import Any

from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

from src.graph import FanInNode, ParallelNode, StateGraph
from src.graph.state import GraphState
from src.harness.graph_agent_nodes import (
    DEFAULT_MESSAGE_KEY,
    agent_turn_node,
    make_agent_turn_handler,
)


def _test_factory(output: str = "ok") -> Any:
    """TestModel factory:返 Agent(TestModel),不真调 API。"""
    def fac(instructions: str = "", capabilities=None, toolsets=None) -> Agent:
        return Agent(
            TestModel(custom_output_text=output),
            instructions=instructions,
            capabilities=list(capabilities) if capabilities else [],
            toolsets=list(toolsets) if toolsets else [],
        )
    return fac


def _spy_factory(output: str, capture: dict) -> Any:
    """factory + spy:记录 agent.run 收到的 message_history(验证续聊传参)。"""
    def fac(instructions: str = "", capabilities=None, toolsets=None) -> Agent:
        ag = Agent(TestModel(custom_output_text=output), instructions=instructions)
        orig_run = ag.run

        async def spy_run(prompt, **kw):
            capture["message_history"] = kw.get("message_history")
            capture["prompt"] = prompt
            return await orig_run(prompt, **kw)

        ag.run = spy_run  # type: ignore[method-assign]
        return ag

    return fac


# ── 单节点:agent.run + output/messages 回写 ────────────────────────────

def test_agent_turn_node_writes_output_and_messages() -> None:
    node = agent_turn_node("a", agent_factory=_test_factory("hello"))
    state = GraphState(input="hi")

    out = asyncio.run(node.execute(state))

    assert out.output == "hello"
    assert out.current_node == "agent_turn"
    msgs = out.context[DEFAULT_MESSAGE_KEY]
    assert isinstance(msgs, list) and len(msgs) >= 1   # all_messages() 非空


def test_agent_turn_node_passes_input_as_prompt() -> None:
    capture: dict = {}
    node = agent_turn_node("a", agent_factory=_spy_factory("x", capture))
    asyncio.run(node.execute(GraphState(input="the-query")))
    assert capture["prompt"] == "the-query"


# ── 续聊:context history → message_history ─────────────────────────────

def test_continues_from_context_message_history() -> None:
    """续聊:context 存的真 ModelMessage(上轮 all_messages 产)→ 透传 message_history。

    seed 必须是 pydantic-ai ModelMessage(有 conversation_id),不是 dict——封装存的
    本就是 all_messages() 的返回,天然合法。这里先跑一次 seed agent 拿真序列。
    """
    capture: dict = {}
    seed_agent = Agent(TestModel(custom_output_text="seed"))
    seed_msgs = asyncio.run(seed_agent.run("seed-prompt")).all_messages()

    node = agent_turn_node("a", agent_factory=_spy_factory("x", capture))
    state = GraphState(input="more")
    state.context[DEFAULT_MESSAGE_KEY] = seed_msgs

    asyncio.run(node.execute(state))

    assert capture["message_history"] is seed_msgs    # context history 透传 Agent.run


def test_empty_context_defaults_to_fresh_history() -> None:
    capture: dict = {}
    node = agent_turn_node("a", agent_factory=_spy_factory("x", capture))
    asyncio.run(node.execute(GraphState(input="hi")))
    assert capture["message_history"] == []      # 无 history → 空 list(新对话)


# ── 多 agent 拓扑:graph.run 编排(结合的精华)──────────────────────────

def test_two_node_graph_runs_both_agents_in_sequence() -> None:
    """线性 graph:research → write,两节点都是 agent_turn_node。证明 graph 编排 +
    Agent 节点结合(每节点独立 Agent run,tool 循环归各自 Agent 内部)。"""
    g = StateGraph("two-agent")
    g.add_node("research", agent_turn_node("research", agent_factory=_test_factory("R")))
    g.add_node("write", agent_turn_node("write", agent_factory=_test_factory("W")))
    g.add_edge("research", "write")
    g.set_entry_point("research")

    result = asyncio.run(g.run(GraphState(input="task")))

    assert result.output == "W"                   # 末节点产出
    assert result.status == "done"
    assert "research" in g.list_nodes()
    assert "write" in g.list_nodes()


def test_parallel_fanin_graph_each_branch_runs_agent() -> None:
    """多 agent 拓扑:ParallelNode(2 分支,各一个 agent_turn_node)→ FanInNode(best)。
    graph 的真价值——并行多视角汇聚,每分支一个 Agent run + capability。"""
    g = StateGraph("parallel-agent")
    g.add_node(
        "views",
        ParallelNode(
            "views",
            branches={
                "opt1": [agent_turn_node("opt1", agent_factory=_test_factory("short"))],
                "opt2": [agent_turn_node("opt2", agent_factory=_test_factory("longer-output-wins"))],
            },
        ),
    )
    g.add_node("merge", FanInNode("merge", source_name="views", merge_mode="best"))
    g.add_edge("views", "merge")
    g.set_entry_point("views")

    result = asyncio.run(g.run(GraphState(input="topic")))

    # best = 最长 output("longer-output-wins" > "short")
    assert result.output == "longer-output-wins"
    assert result.parallel_results["views"]                       # 两分支都跑了
    assert len(result.parallel_results["views"]) == 2


def test_make_handler_returns_callable_wrappable_in_function_node() -> None:
    """make_agent_turn_handler 裸 handler 可直接喂 FunctionNode(等价 agent_turn_node)。"""
    from src.graph.nodes import FunctionNode

    handler = make_agent_turn_handler(agent_factory=_test_factory("raw"))
    node = FunctionNode("raw", handler)
    out = asyncio.run(node.execute(GraphState(input="hi")))
    assert out.output == "raw"
