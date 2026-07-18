"""P2 ProfileCapability 单测:get_instructions == profile.compile + 挂 Agent 注入。

ponytail:纯函数断言(get_instructions)+ TestModel 挂 Agent 验证 instructions 注入
(不真调 LLM,TestModel 是 pydantic-ai 内存 model)。
"""

from __future__ import annotations

import asyncio

from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

from src.agent.profile import AgentBaseProfile, LayerProfile
from src.harness.capabilities import ProfileCapability


def test_profile_capability_get_instructions_equals_compile() -> None:
    p = AgentBaseProfile(agent_id="t")
    p.add_layer(LayerProfile(layer=0, source="soul", content="You are Sage.", priority=100))
    p.add_layer(LayerProfile(layer=2, source="rules", content="Be terse.", priority=80))
    cap = ProfileCapability(profile=p)
    assert cap.id == "profile"
    assert cap.defer_loading is False          # 常驻,不 defer
    assert cap.get_instructions() == p.compile()
    assert "You are Sage." in cap.get_instructions()
    assert "Be terse." in cap.get_instructions()


def test_profile_capability_empty_profile_is_noop() -> None:
    cap = ProfileCapability(profile=None)
    assert cap.get_instructions() == ""        # 无 profile → 空指令(no-op)


def test_profile_capability_attaches_and_injects_instructions() -> None:
    """挂 native Agent(TestModel),跑一次确认 capability accept + instructions 进 system prompt。"""
    p = AgentBaseProfile(agent_id="t")
    p.add_layer(LayerProfile(layer=1, source="id", content="IDENTITY-MARKER-XYZ", priority=90))
    agent = Agent(TestModel(), capabilities=[ProfileCapability(profile=p)])
    result = asyncio.run(agent.run("hi"))
    # instructions 经 capability 注入 system prompt,出现在 message 历史里
    blob = repr(result.all_messages())
    assert "IDENTITY-MARKER-XYZ" in blob
