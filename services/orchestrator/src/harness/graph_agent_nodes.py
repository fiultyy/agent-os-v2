"""graph loop ↔ pydantic-ai 2 桥接节点(ADR: graph 聚焦编排器,Agent 作执行基建)。

设计依据(见 docs/adr/pydantic-ai-v2-adoption.md「后续:graph 聚焦」):
graph loop 退役「单 agent 内部 tool 循环」——老 ``chat.py`` 的 ``_node_llm`` /
``_node_tool`` / ``llm_synthesize`` + ``tool_iteration`` / ``tool_use_history`` /
``needs_tool`` 条件边(``tool→llm`` 循环到 ``MAX_TOOL_ITERATIONS``)与 pydantic-ai
``CallToolsNode`` 完全重叠。这部分归 Agent 内部(Agent.run 自动 tool 循环)。

graph loop 聚焦它的真价值——**多 agent 拓扑**(``ParallelNode`` / ``FanInNode`` /
``SubgraphNode`` / checkpoint / resume),这是 pydantic-ai 单 Agent run 没有的。

本模块是结合点:把「跑一轮 pydantic-ai Agent.run」封装成 graph 节点。复用现有
:class:`FunctionNode`(不新建节点基类——rung:已 in-codebase)。graph 用户声明拓扑,
每个节点一个 Agent run,自动获得 capability 横切(observe/guardrail/profile/memory/skill)。

典型用法(单 agent graph,tool 循环归 Agent)::

    g = StateGraph("native-turn")
    g.add_node("agent", agent_turn_node(capabilities=[observe, guardrail]))
    g.set_entry_point("agent")
    result = await g.run(GraphState(input="hello"))

多 agent 拓扑(parallel fan-out + fan-in,每分支一个 Agent)::

    g.add_node("views", ParallelNode("views", branches={
        "opt1": [agent_turn_node("opt1", capabilities=[observe])],
        "opt2": [agent_turn_node("opt2", capabilities=[observe])],
    }))
    g.add_node("merge", FanInNode("merge", source_name="views", merge_mode="best"))
    g.add_edge("views", "merge"); g.set_entry_point("views")
"""

from __future__ import annotations

from typing import Any, Callable, Sequence

from src.graph.nodes import FunctionNode
from src.graph.state import GraphState
from src.harness.native_agent import build_native_agent

# Agent message_history(pydantic-ai ``ModelMessage`` 列表)在 GraphState 的存储 key。
# GraphState.messages 是 v2 老 ``list[dict]`` 格式(供老 _node_llm / memory hooks / 退役
# 的 tool 循环),与 pydantic-ai ModelMessage 类型不兼容——Agent 自管 message_history,
# 续聊 history 存 context,跨节点共享同一对话流。
DEFAULT_MESSAGE_KEY = "_pydantic_messages"


def make_agent_turn_handler(
    instructions: str = "",
    capabilities: Sequence[Any] | None = None,
    toolsets: Sequence[Any] | None = None,
    *,
    agent_factory: Callable[..., Any] = build_native_agent,
    message_key: str = DEFAULT_MESSAGE_KEY,
):
    """造一个 graph 节点 handler:跑一轮 pydantic-ai Agent.run。

    流程::

        state.input → agent.run(message_history=state.context[message_key])
        state.output = result.output
        state.context[message_key] = result.all_messages()   # 续聊 history 回写

    tool 循环 + capability 横切(observe/guardrail/profile/memory/skill)全归 Agent.run
    内部,不再走 graph 层 ``tool_iteration`` / ``needs_tool`` 条件边(退役重叠)。

    Args:
        instructions: Agent system prompt(ProfileCapability 通常覆盖此值)。
        capabilities / toolsets: 注入 Agent(pydantic-ai Capability / FunctionToolset)。
        agent_factory: 造 Agent 的工厂(默认 :func:`build_native_agent` 接智谱 glm)。
            测试可传 TestModel factory,不真调 API。
        message_key: Agent message_history 存 GraphState.context 的 key(续聊复用)。
    """
    caps = list(capabilities) if capabilities else []
    ts = list(toolsets) if toolsets else []

    async def handler(state: GraphState) -> GraphState:
        agent = agent_factory(
            instructions=instructions, capabilities=caps, toolsets=ts
        )
        history = state.context.get(message_key, [])
        result = await agent.run(state.input, message_history=history)
        state.output = result.output
        state.context[message_key] = result.all_messages()
        state.current_node = "agent_turn"
        return state

    return handler


def agent_turn_node(
    name: str = "agent_turn",
    instructions: str = "",
    capabilities: Sequence[Any] | None = None,
    toolsets: Sequence[Any] | None = None,
    *,
    agent_factory: Callable[..., Any] = build_native_agent,
    message_key: str = DEFAULT_MESSAGE_KEY,
) -> FunctionNode:
    """便利:跑一轮 pydantic-ai Agent 的 graph 节点(:class:`FunctionNode` 包装)。

    等价于 ``FunctionNode(name, make_agent_turn_handler(...))``,供拓扑声明处一行接入。
    """
    return FunctionNode(
        name,
        make_agent_turn_handler(
            instructions, capabilities, toolsets,
            agent_factory=agent_factory, message_key=message_key,
        ),
    )
