#!/usr/bin/env python3
"""R2 cache_control 自检(ADR: docs/adr/pydantic-ai-v2-adoption.md P8 R2)。

验证智谱 /api/anthropic 端点对 AnthropicModelSettings cache_control 的处理:

- Part 1(离线,总跑):build_native_agent 的 model_settings 透传 cache 字段
  (anthropic_cache_instructions / tool_definitions)。证明代码层正确设了 cache。
- Part 2(在线,需 ANTHROPIC_AUTH_TOKEN):两轮同 system prompt,第二轮
  usage.cache_read_input_tokens > 0 = 端点命中 cache。未命中 = 端点忽略
  cache_control(零风险降级:不省 token 但不破功能,P8 R2 设计前提)。

运行::

    cd services/orchestrator
    /usr/bin/python3.12 scripts/check_r2_cache.py             # Part1 only
    ANTHROPIC_AUTH_TOKEN=... /usr/bin/python3.12 scripts/check_r2_cache.py   # 含 Part2
"""
from __future__ import annotations

import os
import sys

# services/orchestrator/scripts/ → services/orchestrator/(src 的父,cwd-independent)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


def part1_offline() -> None:
    """离线:build_native_agent model_settings 透传 cache_control 字段。"""
    from src.harness.native_agent import build_native_agent

    agent = build_native_agent(
        instructions="You are a terse assistant.",
        model_settings={
            "anthropic_cache_instructions": "5m",
            "anthropic_cache_tool_definitions": "5m",
        },
    )
    ms = agent.model_settings
    assert ms.get("anthropic_cache_instructions") == "5m", f"instructions cache 缺: {ms}"
    assert ms.get("anthropic_cache_tool_definitions") == "5m", f"tool cache 缺: {ms}"
    print(f"[Part1] ✅ model_settings 透传 OK: {ms}")


def part2_online() -> None:
    """在线:智谱 /api/anthropic 两轮同 system,看第二轮 cache_read 命中。"""
    token = os.getenv("ANTHROPIC_AUTH_TOKEN")
    if not token:
        print("[Part2] SKIP: ANTHROPIC_AUTH_TOKEN 未设(离线环境)。")
        return
    import asyncio

    from pydantic_ai import Agent
    from pydantic_ai.models.anthropic import AnthropicModel
    from pydantic_ai.providers.anthropic import AnthropicProvider

    model = AnthropicModel(
        os.getenv("ANTHROPIC_MODEL", "glm-4.7"),
        provider=AnthropicProvider(
            base_url=os.getenv(
                "ANTHROPIC_BASE_URL", "https://open.bigmodel.cn/api/anthropic"
            ),
            api_key=token,
        ),
    )
    # 拉长 system prompt 触发 cache 阈值(anthropic cache 需 ≥1024 tokens)
    sys_prompt = "You are a terse assistant. Reply in one word. " * 200
    agent = Agent(
        model,
        instructions=sys_prompt,
        model_settings={"anthropic_cache_instructions": "5m"},
    )

    async def _two_turns():
        r1 = await agent.run("say hi")
        u1 = r1.usage
        r2 = await agent.run("say hi", message_history=r1.all_messages())
        u2 = r2.usage
        return u1, u2

    u1, u2 = asyncio.run(_two_turns())
    print(f"[Part2] turn1 usage: {u1}")
    print(f"[Part2] turn2 usage: {u2}")
    cache_read = getattr(u2, "cache_read_tokens", 0) or 0
    cache_creation = getattr(u2, "cache_creation_tokens", 0) or 0
    if cache_read > 0:
        print(f"[Part2] ✅ cache 命中: cache_read_input_tokens={cache_read}")
    else:
        print(
            f"[Part2] ⚠️  端点未命中 cache(cache_read=0, creation={cache_creation}) —— "
            "忽略 cache_control,零风险降级(不省 token 但不破功能)"
        )


if __name__ == "__main__":
    part1_offline()
    part2_online()
