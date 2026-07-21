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
