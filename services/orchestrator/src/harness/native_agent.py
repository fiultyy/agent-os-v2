"""Native in-process pydantic-ai 2.0 Agent — agent-os-v2 自研 agent 的执行内核。

ADR: docs/adr/pydantic-ai-v2-adoption.md(P1 骨架)。

orche 内置 LLM(智谱 glm via openai-compatible paas/v4)跑 pydantic-ai Agent。
这条 native 路径才让 2.0 的 Capability/defer_loading/event-stream 生效——
外部 harness(claw/claude-code,见 claude.py/openclaw.py)是黑盒子进程/远程 gateway,
2.0 三件套管不到,保留不动(ADR 红线)。

后续阶段注入 capabilities:
  P2 ProfileCapability / P3 MemoryCapability / P4 GuardrailCapability /
  P5 ObserveCapability / P6 SkillCapabilityFactory。

LLM 接入复用 v2 现有 env(同 LLMClient):LLM_BASE_URL / LLM_API_KEY / LLM_MODEL,
默认智谱 paas/v4 + glm-4-flash。
"""

from __future__ import annotations

import os
from typing import Any, Sequence

from pydantic_ai import Agent
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.providers.anthropic import AnthropicProvider

HARNESS_TYPE = "agent-os-v2"  # 与 observe/client.py 对齐(agent-os-v2 harness)

# 智谱 glm via anthropic-compatible 通道(/api/anthropic):套餐内可用 + 支持
# cache_control(engine.py 主对话同通道 glm-5-turbo)。openai 通道(coding/paas/v4)
# 忽略 cache_control 且 model 名不同(glm-4-flash)。走 anthropic 协议接。
_DEFAULT_BASE_URL = "https://open.bigmodel.cn/api/anthropic"


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
) -> Agent:
    """组装 native in-process Agent。

    capabilities / toolsets 在 P2-P6 注入;P1 骨架空跑,先验证 Agent + glm 连通。
    """
    return Agent(
        build_model(),
        instructions=instructions,
        capabilities=list(capabilities) if capabilities else [],
        toolsets=list(toolsets) if toolsets else [],
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
