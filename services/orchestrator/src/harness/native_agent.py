"""Native in-process pydantic-ai 2.0 Agent — agent-os-v2 自研 agent 的执行内核。

ADR: docs/adr/pydantic-ai-v2-adoption.md(P1 骨架)。

orche 内置 LLM(智谱 glm via anthropic-compatible ``/api/anthropic``)跑 pydantic-ai Agent。
这条 native 路径才让 2.0 的 Capability/defer_loading/event-stream 生效——
外部 harness(claw/claude-code,见 claude.py/openclaw.py)是黑盒子进程/远程 gateway,
2.0 三件套管不到,保留不动(ADR 红线)。

后续阶段注入 capabilities:
  P2 ProfileCapability / P3 MemoryCapability / P4 GuardrailCapability /
  P5 ObserveCapability / P6 SkillCapabilityFactory。

LLM 接入 env:``ANTHROPIC_BASE_URL`` / ``ANTHROPIC_AUTH_TOKEN`` / ``ANTHROPIC_MODEL``,
默认智谱 ``/api/anthropic`` + glm-4.7(见 ``build_model``)。
"""

from __future__ import annotations

import logging
import os
from typing import Any, Sequence

from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.providers.anthropic import AnthropicProvider

from .capabilities.engineering_discipline_capability import EngineeringDisciplineCapability
from .mcp_config import build_mcp_toolset, build_mcp_toolsets, load_global_mcp_servers

logger = logging.getLogger(__name__)

HARNESS_TYPE = "agent-os-v2"  # 与 observe/client.py 对齐(agent-os-v2 harness)

# 智谱 glm via anthropic-compatible 通道(/api/anthropic):套餐内可用 + 支持
# cache_control(engine.py 主对话同通道 glm-5-turbo)。openai 通道(coding/paas/v4)
# 忽略 cache_control 且 model 名不同(glm-4-flash)。走 anthropic 协议接。
_DEFAULT_BASE_URL = "https://open.bigmodel.cn/api/anthropic"

# ADR(harness-adr.md line114)A/B 旋钮:glm 等非 Claude 模型对 CC 5 条有效性无 ab,留 opt-out。
# 进程级读一次(启动);改需重启。EngineeringDisciplineCapability 构造参数 enabled 可覆盖。
_DISCIPLINE_DISABLED = os.getenv("AO2_DISCIPLINE_DISABLED", "").lower() in ("1", "true", "yes", "on")


def build_model(model_name: str | None = None) -> AnthropicModel:
    """智谱 glm via anthropic-compatible endpoint(/v1/messages)。

    model_name:可选模型名覆盖。``None``(默认)→ 读 ``ANTHROPIC_MODEL`` env(glm-4.7);
    非空 → 用此名(其余 base_url/api_key 仍走 env)。让 ``build_native_agent`` 在不改
    env 的前提下覆盖单次 spawn 的模型(子代理配置 model / A2A 路由模型协商)。
    """
    base_url = os.getenv("ANTHROPIC_BASE_URL", _DEFAULT_BASE_URL)
    api_key = os.getenv("ANTHROPIC_AUTH_TOKEN", "")
    if not api_key:
        # 明确报错(否则 anthropic-sdk 误导 'set ANTHROPIC_API_KEY',本代码读 _AUTH_TOKEN)
        raise RuntimeError(
            "native_agent needs ANTHROPIC_AUTH_TOKEN env (智谱 glm anthropic key)"
        )
    resolved = model_name or os.getenv("ANTHROPIC_MODEL", "glm-4.7")
    return AnthropicModel(
        resolved,
        provider=AnthropicProvider(base_url=base_url, api_key=api_key),
    )


def build_native_agent(
    instructions: str = "",
    capabilities: Sequence[Any] | None = None,
    toolsets: Sequence[Any] | None = None,
    model_settings: Any = None,
    model_name: str | None = None,
    mcp_servers: Sequence[dict[str, Any]] | None = None,
    output_type: type[BaseModel] | None = None,
) -> Agent:
    """组装 native in-process Agent。

    capabilities / toolsets 在 P2-P6 注入;P1 骨架空跑,先验证 Agent + glm 连通。
    model_settings:P8 R2 cache_control(AnthropicModelSettings,如
    anthropic_cache_instructions/tool_definitions="5m"),替代老 ContextCompiler/static_count。

    model_name:可选模型名覆盖。``None``(默认)→ ``build_model`` 读 ``ANTHROPIC_MODEL`` env
    (glm-4.7);非空 → 用此名建 AnthropicModel(其余 base_url/api_key 仍走 env)。语义:让调用方
    (如 ``run_agent_turn`` 子代理配置指定的 model)在不改 env 的前提下覆盖单次 spawn 的模型。
    A2A 铺路:统一 spawn 入口,未来 A2A 协议发现的外部 agent 仍走 ``build_native_agent``。

    mcp_servers(5B):MCP server 配置列表,每项形如::

        {
          "name": "echo",                    # 工具前缀(多 server 去歧义)
          "transport": "stdio" | "sse" | "streamable_http",
          # stdio:command + args + env(可选)
          "command": "python", "args": ["-m", "echo_mcp"], "env": {"K": "v"},
          # sse / streamable_http:url + headers(可选)
          "url": "http://localhost:8000/sse", "headers": {"Authorization": "Bearer x"},
        }

    按配置构造 ``MCPToolset``(stdio→``StdioTransport``;sse/http→URL 直传 pydantic-ai
    ``MCPToolset``,后者自行按 URL 推断 SSE vs streamable_http;transport 显式时可省 url
    scheme 推断)。toolset 经 ``.prefixed(name)`` 加前缀防跨 server 重名。Agent.run
    自动进入/退出 toolset(``AsyncExitStack`` 管理生命周期,见 pydantic-ai Agent.run
    ``exit_stack.enter_async_context(toolset)``),调用方无需手动 connect/disconnect。

    配置来源(ponytail):agent 配置(``_state.agents[...]`` 里的 ``mcp_servers`` 字段,
    经 ``run_agent_turn`` / ``_build_native_session`` 透传)优先;全局 ``.mcp.json``
    fallback 留给后续(见 ``docs/mcp-config-template.md``)。

    output_type(P2 schema-registry):可选 ``type[BaseModel]``,非 None 时透传
    ``Agent(output_type=...)`` 走 pydantic-ai 结构化输出(``result.output`` 是 BaseModel
    实例)。``None``(默认)→ str passthrough,现有 6 caller 不传零影响。workflow_engine
    经 ``resolve_schema(node.schema_ref)`` 解析后透传。

    ADR(harness-adr.md 第一层「工程纪律」):默认 prepend EngineeringDisciplineCapability
    (CC 5 条 + context-mgmt,进 stable ``dynamic=False`` 段,模型每轮可见、可 cache)。
    覆盖边界:所有 ``build_native_agent`` 调用方(harness routes / 老 chat / graph 多 agent
    经 agent_factory / smoke / ``run_agent_turn`` 子代理收敛)自动注入。
    纪律段在 capability-tier 内居首,调用方传非空 instructions 时排在其后(非 instructions 绝对首段)。

    覆盖/换文本通道(ADR line114 A/B):去重守卫下,调用方传 ``EngineeringDisciplineCapability``
    实例即覆盖默认(原 ad-hoc override);capability 的 ``enabled`` / ``discipline_text`` 字段
    + ``AO2_DISCIPLINE_DISABLED`` env 旋钮把 ad-hoc 正式化(进程级读一次)。

    cache 隐性耦合(ADR-1):``instructions`` 透传 ``Agent(instructions=...)`` base 段,pydantic-ai
    ``_get_instructions`` 把 base+cap instructions ``\\n``.join 成单个
    ``InstructionPart(dynamic=False)``,整体被 ``anthropic_cache_instructions="5m"`` 整段缓存。
    故 **``instructions`` 必须永远静态字符串** —— 若改 ``dynamic=True``(或换 callable/callable-with-state),
    整段(含 cap)翻 dynamic 丢 cache 无告警(**传染性**,与 ``EngineeringDisciplineCapability``
    模块 docstring 同款)。来源 = agents.yaml 静态字段(非文件、非 dynamic),高频变动内容走 defer
    skill 而非 instructions。
    """
    caps = list(capabilities) if capabilities else []
    if not any(isinstance(c, EngineeringDisciplineCapability) for c in caps):
        # 默认注入(env 旋钮关段;调用方显式传实例则去重=override 通道,见 docstring)
        caps.insert(0, EngineeringDisciplineCapability(enabled=not _DISCIPLINE_DISABLED))
    # 5B:MCP server 配置 → toolset 列表(与调用方传入的 toolsets 并列)。
    ts = list(toolsets) if toolsets else []
    if mcp_servers:
        ts.extend(build_mcp_toolsets(mcp_servers))
    model = build_model(model_name) if model_name else build_model()
    # output_type=None 不能直传 Agent(pydantic-ai 视作显式空 schema 抛 UserError);
    # 省略 → Agent 默认 str passthrough。非 None 才透传走结构化输出。
    if output_type is not None:
        return Agent(
            model,
            instructions=instructions,
            capabilities=caps,
            toolsets=ts,
            model_settings=model_settings or {},
            output_type=output_type,
        )
    return Agent(
        model,
        instructions=instructions,
        capabilities=caps,
        toolsets=ts,
        model_settings=model_settings or {},
    )



async def run_agent_turn_with_stop(
    agent: Agent,
    prompt: str,
    *,
    session_id: str,
    agent_id: str,
    bus: Any = None,
    message_history: Any = None,
    metadata: dict[str, Any] | None = None,
) -> Any:
    """Run a native ``Agent.run`` and fire ``STOP`` on completion (ADR-2 H2).

    Thin lifecycle wrapper: every native agent turn (harness ``/h turn`` sync
    + async, ``/v1/execute``) routes through here so the STOP event has one
    fire point (in ``native_agent``), not three duplicated ones scattered in
    the route layer. ``bus`` is the orchestrator ``MemoryEventBus``
    (``_state.memory_event_bus``); ``None`` skips the STOP fire (e.g. the
    ``__main__`` smoke / unit tests that build a bare agent).

    STOP fires *after* ``agent.run`` returns, best-effort; a failed turn
    propagates to the caller without a STOP fire (no completed run).
    """
    result = await agent.run(
        prompt, message_history=message_history, metadata=metadata or {},
    )
    if bus is not None:
        try:
            from src.memory.event_bus import EventType
            from src.memory.hooks import StopContext
            await bus.emit(
                EventType.STOP,
                StopContext(session_id=session_id, agent_id=agent_id),
            )
        except Exception:
            logger.warning("STOP event fire failed (non-fatal)", exc_info=True)
    return result


if __name__ == "__main__":
    # P1 smoke:空 capability 跑通 run('hello'),验证 glm 连通 + pydantic-ai 依赖可用。
    import asyncio

    async def _smoke() -> None:
        agent = build_native_agent(
            instructions="You are a terse assistant. Reply in one short sentence."
        )
        result = await agent.run("用一句话说你好。")
        print("native_agent smoke OK:", result.output)

    asyncio.run(_smoke())
