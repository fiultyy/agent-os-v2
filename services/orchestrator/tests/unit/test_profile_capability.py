"""P2 ProfileCapability / ADR-1 LayerCapability 单测。

ponytail:纯函数断言(get_instructions)+ TestModel 挂 Agent 验证 instructions 注入
(不真调 LLM,TestModel 是 pydantic-ai 内存 model)。
"""

from __future__ import annotations

import asyncio

from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

from src.agent.profile import AgentBaseProfile, LayerProfile
from src.harness.capabilities import LayerCapability, ProfileCapability, make_profile_capabilities


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


# ── ADR-1: LayerCapability + make_profile_capabilities ───────────────────


def test_layer_capability_get_instructions_format() -> None:
    """LayerCapability.get_instructions 返 `=== source (Lx) ===\n{content}` 格式。"""
    cap = LayerCapability(layer=2, source="agents_md_guidelines", content="Be terse.")
    assert cap.id == "layer"
    assert cap.defer_loading is False          # ADR-1 红线:常驻
    assert cap.get_instructions() == "=== agents_md_guidelines (L2) ===\nBe terse."


def test_make_profile_capabilities_none_returns_empty() -> None:
    """profile None → [](routes None-safe)。"""
    assert make_profile_capabilities(None) == []


def test_make_profile_capabilities_l0_to_l4_order() -> None:
    """每 LayerProfile → 一条 LayerCapability,按 L0→L4 序生成(同层 priority 序保留)。"""
    p = AgentBaseProfile(agent_id="t")
    p.add_layer(LayerProfile(layer=2, source="rules", content="RULES-L2", priority=80))
    p.add_layer(LayerProfile(layer=0, source="soul", content="SOUL-L0", priority=100))
    p.add_layer(LayerProfile(layer=1, source="id", content="ID-L1", priority=90))
    caps = make_profile_capabilities(p)
    # 顺序:L0 → L1 → L2(与 AgentBaseProfile.compile 一致)
    assert [(c.layer, c.source) for c in caps] == [
        (0, "soul"), (1, "id"), (2, "rules"),
    ]
    # get_instructions 各层格式正确
    assert caps[0].get_instructions() == "=== soul (L0) ===\nSOUL-L0"
    assert caps[2].get_instructions() == "=== rules (L2) ===\nRULES-L2"
    # 常驻红线
    assert all(c.defer_loading is False for c in caps)


def test_make_profile_capabilities_multiple_per_layer() -> None:
    """同层多 LayerProfile(priority 序)→ 多条 LayerCapability,顺序保留。"""
    p = AgentBaseProfile(agent_id="t")
    p.add_layer(LayerProfile(layer=1, source="b", content="B", priority=50))
    p.add_layer(LayerProfile(layer=1, source="a", content="A", priority=90))  # priority 高 → 先
    caps = make_profile_capabilities(p)
    assert [c.source for c in caps] == ["a", "b"]   # priority 降序(add_layer 已 sort)
