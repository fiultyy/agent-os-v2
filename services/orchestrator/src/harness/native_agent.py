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

import os
from typing import Any, Sequence

from pydantic_ai import Agent
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.providers.anthropic import AnthropicProvider

from .capabilities.engineering_discipline_capability import EngineeringDisciplineCapability

HARNESS_TYPE = "agent-os-v2"  # 与 observe/client.py 对齐(agent-os-v2 harness)

# 智谱 glm via anthropic-compatible 通道(/api/anthropic):套餐内可用 + 支持
# cache_control(engine.py 主对话同通道 glm-5-turbo)。openai 通道(coding/paas/v4)
# 忽略 cache_control 且 model 名不同(glm-4-flash)。走 anthropic 协议接。
_DEFAULT_BASE_URL = "https://open.bigmodel.cn/api/anthropic"

# ADR(harness-adr.md line114)A/B 旋钮:glm 等非 Claude 模型对 CC 5 条有效性无 ab,留 opt-out。
# 进程级读一次(启动);改需重启。EngineeringDisciplineCapability 构造参数 enabled 可覆盖。
_DISCIPLINE_DISABLED = os.getenv("AO2_DISCIPLINE_DISABLED", "").lower() in ("1", "true", "yes", "on")


def build_model() -> AnthropicModel:
    """智谱 glm via anthropic-compatible endpoint(/v1/messages)。"""
    base_url = os.getenv("ANTHROPIC_BASE_URL", _DEFAULT_BASE_URL)
    api_key = os.getenv("ANTHROPIC_AUTH_TOKEN", "")
    if not api_key:
        # 明确报错(否则 anthropic-sdk 误导 'set ANTHROPIC_API_KEY',本代码读 _AUTH_TOKEN)
        raise RuntimeError(
            "native_agent needs ANTHROPIC_AUTH_TOKEN env (智谱 glm anthropic key)"
        )
    model_name = os.getenv("ANTHROPIC_MODEL", "glm-4.7")
    return AnthropicModel(
        model_name,
        provider=AnthropicProvider(base_url=base_url, api_key=api_key),
    )


def build_native_agent(
    instructions: str = "",
    capabilities: Sequence[Any] | None = None,
    toolsets: Sequence[Any] | None = None,
    model_settings: Any = None,
) -> Agent:
    """组装 native in-process Agent。

    capabilities / toolsets 在 P2-P6 注入;P1 骨架空跑,先验证 Agent + glm 连通。
    model_settings:P8 R2 cache_control(AnthropicModelSettings,如
    anthropic_cache_instructions/tool_definitions="5m"),替代老 ContextCompiler/static_count。

    ADR(harness-adr.md 第一层「工程纪律」):默认 prepend EngineeringDisciplineCapability
    (CC 5 条 + context-mgmt,进 stable ``dynamic=False`` 段,模型每轮可见、可 cache)。
    覆盖边界:所有 ``build_native_agent`` 调用方(harness routes / 老 chat / graph 多 agent
    经 agent_factory / smoke)自动注入;**meta-agent transient subagent(``run_agent_turn``)
    不经此处,不覆盖**(见 capability docstring;agent_runner.py R2 红线,改它超 scope)。
    纪律段在 capability-tier 内居首,调用方传非空 instructions 时排在其后(非 instructions 绝对首段)。

    覆盖/换文本通道(ADR line114 A/B):去重守卫下,调用方传 ``EngineeringDisciplineCapability``
    实例即覆盖默认(原 ad-hoc override);capability 的 ``enabled`` / ``discipline_text`` 字段
    + ``AO2_DISCIPLINE_DISABLED`` env 旋钮把 ad-hoc 正式化(进程级读一次)。
    """
    caps = list(capabilities) if capabilities else []
    if not any(isinstance(c, EngineeringDisciplineCapability) for c in caps):
        # 默认注入(env 旋钮关段;调用方显式传实例则去重=override 通道,见 docstring)
        caps.insert(0, EngineeringDisciplineCapability(enabled=not _DISCIPLINE_DISABLED))
    return Agent(
        build_model(),
        instructions=instructions,
        capabilities=caps,
        toolsets=list(toolsets) if toolsets else [],
        model_settings=model_settings or {},
    )


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
