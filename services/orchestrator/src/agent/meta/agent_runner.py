"""run_agent_turn — 公共子 agent 单轮执行函数(经 build_native_agent 组装器收敛)。

3B 收敛点:子代理不再手搓 ``messages=[{system},{user}]`` + ``llm_client.chat``,而是走
与主 agent 同一个组装入口 :func:`src.harness.native_agent.build_native_agent` ——
自动得到「纪律段(CC 5 条)默认 prepend + ToolBridge 全覆盖 + (可选)ObserveCapability 子代理事件」。
副作用:子代理配置的 ``model`` 现在透传给 ``build_native_agent(model_name=...)`` 覆盖默认模型;
原本的 ``llm.chat`` 直连路径退役。A2A 铺路:未来 A2A 协议发现的外部 agent 走同一入口。

设计目标:把一个 *临时* subagent 跑一轮 LLM 对话并返回响应字符串,**完全不碰**
顶层 agent 的 GraphState / context / 记忆模块 —— 子 agent 是临时态,记忆只在
fan-in 后由主 agent 单点落库。

红线
----
R1 (记忆零触碰):
    本函数体 *不调用* ``memory_event_bus.emit`` / ``_trigger_ingest`` /
    ``_trigger_kg_extraction`` / ``memory_service.*`` 中的任何一个,也 *不挂*
    ``MemoryWriterCapability``(子代理不沉淀记忆)。grep 本文件零 memory 调用即守恒此红线。

R2 (主路径冻结):
    本函数 *不 import 也不修改* ``_build_execution_graph`` / ``_node_llm`` /
    ``chat.py`` 的 ``/execute`` 线性图。``build_native_agent`` 不是 R2 禁项 —— R2 只
    禁主路径线性图;组装器是 ADR-sanctioned 的统一 spawn 入口。

上下文隔离:
    独立 ``agent_id`` + 独立 pydantic-ai Agent(本地 messages)。不复用顶层 agent 的
    GraphState/messages/context,避免串味。
"""

from __future__ import annotations

import logging
from typing import Any

from src.harness.capabilities import ObserveCapability, ToolBridgeCapability
from src.harness.native_agent import build_native_agent
from src.services import _state

logger = logging.getLogger(__name__)


async def run_agent_turn(
    agent_id: str,
    input: str,
    session_id: str,
    system_prompt: str | None = None,
) -> str:
    """Run one LLM turn for a transient subagent and return the response.

    经 ``build_native_agent`` 组装 native in-process Agent(纪律段 + ToolBridge +
    可选 ObserveCapability),跑 ``agent.run(input)`` 返回 ``result.output``。
    子代理是临时态,**不挂 MemoryWriterCapability**(R1:子代理不沉淀记忆,记忆
    在 fan-in 后由主 agent 单点落库)。

    Args:
        agent_id: Subagent id (as returned by ``agent_manager.create_subagent``).
        input: The user/task input for this turn. 空串时用占位 ``"(no task input)"``
            (空 messages 被智谱/Anthropic 通道拒为 400 code 1214)。
        session_id: Session id for logging/correlation + ObserveCapability 上报;
            *no* memory hook attaches to it here (memory writes happen fan-in).
        system_prompt: Optional system prompt override. When ``None`` the prompt
            is taken from the subagent's stored config, falling back to a neutral
            default.

    Returns:
        The assistant's reply as a string. On a *non-fatal* degradation (agent
        run failed) an error string is returned rather than raised — the
        orchestration caller surfaces it via ``subgraph_results`` without
        aborting sibling agents.
    """
    # Pull subagent config from the in-memory registry (None-safe). A subagent is
    # created by create_subagent and lives in _state.agents until teardown.
    agent_cfg: dict[str, Any] | None = _state.agents.get(agent_id)
    if agent_cfg is not None:
        cfg_system = agent_cfg.get("system_prompt") or ""
        cfg_model = agent_cfg.get("model")
        cfg_mcp = agent_cfg.get("mcp_servers") or None  # 5B:agent 配置 inline MCP
    else:
        cfg_system = ""
        cfg_model = None
        cfg_mcp = None

    # Effective system prompt: explicit arg > stored config > neutral default.
    # 进 build_native_agent 作 instructions(组装器自动 prepend 纪律段 + tool 桥接)。
    effective_system = system_prompt if system_prompt is not None else cfg_system
    if not effective_system:
        effective_system = "You are a helpful sub-agent. Complete the assigned task."

    # ── Assemble native Agent via the unified spawn entry (A2A 铺路) ───────────
    capabilities: list[Any] = [
        ToolBridgeCapability(
            tool_executor=_state.tool_executor,
            pitfail_registry=_state.pitfail_registry,
        ),
    ]
    # ObserveCapability(可选):把子代理 run 事件转发到 observe-service(子代理自己的
    # harness_id 区分,fire-and-forget —— observe 不可达零回归)。复用 _state 上的
    # ObserveEmitter(若通电);None 则跳过,事件仍 forward 只是不上报 observe。
    emitter = _state.memory_observe_emitter
    if emitter is not None:
        capabilities.append(ObserveCapability(
            emitter=emitter,
            harness_id=f"sub_{agent_id[:8]}",
            session_id=session_id,
        ))
    # R1:不挂 MemoryWriterCapability(子代理不沉淀记忆)。

    agent = build_native_agent(
        instructions=effective_system,
        capabilities=capabilities,
        model_name=cfg_model,  # 子代理配置 model 覆盖;None → build_model 读 env
        mcp_servers=cfg_mcp,  # 5B:agent 配置 inline MCP server(优先于全局 .mcp.json)
    )

    # 防御:input 空时加占位。空 messages 被智谱/Anthropic 通道拒为 400 code 1214。
    task_input = input if input else "(no task input)"

    try:
        result = await agent.run(task_input)
    except Exception as exc:  # noqa: BLE001 — Agent.run 失败降级,不 abort sibling agents
        logger.warning("run_agent_turn: agent.run failed (agent=%s): %s", agent_id, exc)
        return f"[run_agent_turn error] agent run failed: {exc}"

    return result.output or ""
