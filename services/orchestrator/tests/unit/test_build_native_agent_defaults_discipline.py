"""build_native_agent 默认注入工程纪律段 + 去重 + opt-out 旋钮(哨兵,mustFix#3)。

patch ``build_model``(TestModel)免 token/API。断言 ``_cap_instructions`` 含 CC 5 条 +
去重 + ``enabled`` / ``discipline_text`` / ``AO2_DISCIPLINE_DISABLED`` 旋钮。
为下次改动留哨兵:若有人误删默认注入或双 prepend,这里立刻红。
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from pydantic_ai.models.test import TestModel


def _cap_text(agent) -> str:
    """组装后 capability instructions 拼接(私有 ``_cap_instructions``,str 列表)。"""
    return " ".join(
        c if isinstance(c, str) else getattr(c, "content", "")
        for c in agent._cap_instructions
    )


@pytest.fixture(autouse=True)
def _fake_model():
    """patch build_model 返 TestModel(免 ANTHROPIC_AUTH_TOKEN + 真调 API)。"""
    with patch("src.harness.native_agent.build_model", lambda: TestModel()):
        yield


def test_default_injects_cc5_rules() -> None:
    from src.harness.native_agent import build_native_agent

    txt = _cap_text(build_native_agent(instructions="hi"))
    assert "Tools over shell" in txt
    assert "Parallelize independent" in txt
    assert "file_path:line_number" in txt
    assert "Match surrounding style" in txt
    assert "Report faithfully" in txt


def test_dedup_on_explicit_instance() -> None:
    """调用方显式传同类型实例 → 去重,不双 prepend(override 通道)。"""
    from src.harness.capabilities import EngineeringDisciplineCapability
    from src.harness.native_agent import build_native_agent

    agent = build_native_agent(
        instructions="x", capabilities=[EngineeringDisciplineCapability()],
    )
    assert _cap_text(agent).count("Engineering Discipline") == 1


def test_enabled_false_returns_empty() -> None:
    from src.harness.capabilities import EngineeringDisciplineCapability

    assert EngineeringDisciplineCapability(enabled=False).get_instructions() == ""


def test_discipline_text_override() -> None:
    from src.harness.capabilities import EngineeringDisciplineCapability

    cap = EngineeringDisciplineCapability(discipline_text="CUSTOM-RULE-MARKER")
    assert cap.get_instructions() == "CUSTOM-RULE-MARKER"


def test_env_disabled_opt_out(monkeypatch: pytest.MonkeyPatch) -> None:
    """AO2_DISCIPLINE_DISABLED(env 进程级常量)→ build_native_agent 注入 enabled=False 关段。"""
    import src.harness.native_agent as nat

    monkeypatch.setattr(nat, "_DISCIPLINE_DISABLED", True)
    agent = nat.build_native_agent(instructions="hi")
    assert "Tools over shell" not in _cap_text(agent)


def test_l115_boundary_strip_known_divergence_documented() -> None:
    """P5 哨兵(ADR L115):module docstring 必须显式标 boundary strip known-divergence。

    ADR L115 要求 transport 层无条件 strip boundary 标记。AO2 boundary =
    pydantic-ai InstructionPart(dynamic=False),非文本 marker,无 leak 向量 → strip 不适用。
    docstring 标 known-divergence,不硬补 strip(YAGNI)。若有人删该声明或误加 strip,
    本测立即红,逼其先读 ADR L115 决策再动。
    """
    from src.harness.capabilities import engineering_discipline_capability as mod

    doc = mod.__doc__ or ""
    # 关键决策锚点齐全(任一缺失 = docstring 回归)
    assert "ADR L115" in doc
    assert "InstructionPart" in doc
    assert "dynamic=False" in doc
    assert "known-divergence" in doc
    assert "no marker to strip" in doc


def test_l115_no_textual_boundary_marker_in_output() -> None:
    """P5 哨兵:capability 输出是纯纪律文本,不含任何可注入的文本 boundary delimiter。

    这印证「AO2 boundary 非文本 marker」的事实前提 —— 无 ``<<<``/``>>>``/``` ` `` 包裹的
    boundary marker,故 ADR L115 strip 无对象。若有人未来在输出里加文本 marker,本测红,
    逼其先补 strip(届时 known-divergence 不再适用)。
    """
    from src.harness.capabilities import EngineeringDisciplineCapability

    out = EngineeringDisciplineCapability().get_instructions()
    # 没有任何形如 <<<...>>> 或 [[[...]]] 的文本 boundary marker
    assert "<<<" not in out
    assert ">>>" not in out
    assert "[[[" not in out
    assert "]]]" not in out
