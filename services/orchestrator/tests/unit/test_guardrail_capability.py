"""P4 GuardrailCapability 单测:before 拦危险参数 / after 拦敏感输出 / None noop。

用真 Guardrail(纯 regex 无外部依赖),asyncio.run 调 hook(规避 pytest-asyncio mode)。
"""

from __future__ import annotations

import asyncio

import pytest
from pydantic_ai import ModelRetry

from src.harness.capabilities import GuardrailCapability
from src.tools.guardrail import Guardrail


class _Call:
    tool_name = "bash"


class _TD:
    name = "bash"


def test_before_blocks_dangerous_args() -> None:
    cap = GuardrailCapability(guardrail=Guardrail())
    with pytest.raises(ModelRetry):
        asyncio.run(cap.before_tool_execute(
            ctx=None, call=_Call(), tool_def=_TD(), args={"cmd": "rm -rf /"}))


def test_before_allows_safe_args() -> None:
    cap = GuardrailCapability(guardrail=Guardrail())
    out = asyncio.run(cap.before_tool_execute(
        ctx=None, call=_Call(), tool_def=_TD(), args={"cmd": "ls -la"}))
    assert out == {"cmd": "ls -la"}


def test_after_blocks_sensitive_output() -> None:
    cap = GuardrailCapability(guardrail=Guardrail())
    with pytest.raises(ModelRetry):
        asyncio.run(cap.after_tool_execute(
            ctx=None, call=_Call(), tool_def=_TD(), args={},
            result={"password": "hunter2"}))


def test_after_allows_clean_output() -> None:
    cap = GuardrailCapability(guardrail=Guardrail())
    out = asyncio.run(cap.after_tool_execute(
        ctx=None, call=_Call(), tool_def=_TD(), args={}, result="all good"))
    assert out == "all good"


def test_guardrail_none_is_noop() -> None:
    """无 guardrail 注入 → before/after 直通(不拦)。"""
    cap = GuardrailCapability(guardrail=None)
    assert cap.defer_loading is False
    out = asyncio.run(cap.before_tool_execute(
        ctx=None, call=_Call(), tool_def=_TD(), args={"cmd": "rm -rf /"}))
    assert out == {"cmd": "rm -rf /"}
