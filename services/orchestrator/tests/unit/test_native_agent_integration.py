"""P1-P6 集成验收:native Agent 组装多 capability 端到端跑通(TestModel,不真调 glm)。

证明移植完整:Agent + Profile + Guardrail + Observe + Memory 多 capability 可组合到
同一 Agent 协作(profile 注入 system prompt + guardrail 护 tool + observe 出 tick 闭环)。
"""

from __future__ import annotations

import asyncio

from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

from src.agent.profile import AgentBaseProfile, LayerProfile
from src.harness.capabilities import (
    GuardrailCapability,
    ObserveCapability,
    ProfileCapability,
)
from src.tools.guardrail import Guardrail


class _StubEmitter:
    def __init__(self) -> None:
        self.emitted: list[dict] = []

    async def emit(self, ev: dict) -> None:
        self.emitted.append(ev)


def test_native_agent_assembles_profile_guardrail_observe() -> None:
    """多 capability 叠加同一 Agent:profile 注入 + guardrail 护 + observe 闭环。

    (memory/skill 是 defer_loading=False(eager),tool 常驻 wire。原 defer 设计在 glm-5.2
    实测失效(glm 不调 load_capability/search_tools meta-tool)→ 改 eager。toolset 注册在
    P3/P6 单测用 ts.tools introspect 验证,不进 TestModel 集成。)
    """
    profile = AgentBaseProfile(agent_id="native")
    profile.add_layer(LayerProfile(layer=0, source="soul", content="INTEGRATION-MARKER", priority=100))
    em = _StubEmitter()

    agent = Agent(
        TestModel(),
        capabilities=[
            ProfileCapability(profile=profile),
            GuardrailCapability(guardrail=Guardrail()),
            ObserveCapability(emitter=em, harness_id="h1", session_id="s1"),
        ],
    )

    result = asyncio.run(agent.run("hi"))

    # 1) profile 经 get_instructions 注入 system prompt
    assert "INTEGRATION-MARKER" in repr(result.all_messages())
    # 2) observe 闭环:tick_started ... tick_completed(success),outermost 不被短路
    types = [e["event_type"] for e in em.emitted]
    assert types[0] == "tick_started"
    assert types[-1] == "tick_completed"
    assert em.emitted[-1]["data"]["status"] == "success"
    # 3) 全程 agent-os-v2 harness_type
    assert all(e["harness_type"] == "agent-os-v2" for e in em.emitted)


def test_observe_composes_with_other_capability_without_losing_tick_closure() -> None:
    """ObserveCapability(outermost)+ GuardrailCapability 叠加:tick 闭环完整。"""
    profile = AgentBaseProfile(agent_id="native")
    em = _StubEmitter()
    agent = Agent(
        TestModel(),
        capabilities=[
            ObserveCapability(emitter=em, harness_id="h", session_id="s"),
            GuardrailCapability(guardrail=Guardrail()),
        ],
    )
    asyncio.run(agent.run("hi"))
    # observe 仍收到完整闭环(不被 guardrail capability 干扰)
    assert em.emitted[0]["event_type"] == "tick_started"
    assert em.emitted[-1]["event_type"] == "tick_completed"


def test_observe_emits_token_delta_during_streaming() -> None:
    """验证 ObserveCapability 在 Agent.run() 中 emit token_delta。

    TestModel 返回多段 delta，每个 delta 应触发一条 token_delta 事件。
    """
    em = _StubEmitter()
    agent = Agent(
        TestModel(),
        capabilities=[ObserveCapability(emitter=em, harness_id="h", session_id="s")],
    )
    result = asyncio.run(agent.run("用三个词说你好"))
    # 验证 token_delta 事件存在（TestModel 模拟流式返回）
    delta_events = [e for e in em.emitted if e["event_type"] == "token_delta"]
    assert len(delta_events) > 0, "应至少有一条 token_delta"
    # 验证 delta_text 非空
    assert all(e["data"]["delta_text"] for e in delta_events)
    # 验证 tick_completed 包含完整 response
    completed = [e for e in em.emitted if e["event_type"] == "tick_completed"][0]
    assert completed["data"]["response"] == result.output
