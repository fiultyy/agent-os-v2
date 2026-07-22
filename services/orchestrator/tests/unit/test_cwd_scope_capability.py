"""MultiCwdScopeCapability 单测(P1 动态层多 cwd 清单 + set_active_cwd 工具)。

核心三条:
1. get_instructions 含 set_active_cwd 提示 + default 标记(决策 2 无 label 前缀)。
2. set_active_cwd('unknown') → 返错误串(含 'unknown' + 可用 label),不 raise(§10.3)。
3. set_active_cwd('a') 成功 → _active_cwd==Path('/tmp/a') 且 get_session_active_cwd 记录。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from src.agent.agent_registry import ResolvedCwd
from src.harness.capabilities import MultiCwdScopeCapability
from src.tools.cwd_scope import _SESSION_CWD, _active_cwd, get_session_active_cwd


def _run(coro):
    # sync wrapper:新 event loop 避 loop pollution(参考 feedback-pytest-asyncio-loop-pollution)。
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_get_instructions_has_set_active_cwd_and_default_mark() -> None:
    c = MultiCwdScopeCapability(
        cwd_scope=[ResolvedCwd(path_abs="/tmp/a", label="a", default=True)],
        session_key="agent-os-v2:s1",
    )
    instr = c.get_instructions()
    assert "set_active_cwd" in instr
    assert "(default)" in instr
    assert "- a (default) → /tmp/a" in instr


def test_set_active_cwd_unknown_label_returns_error_string_no_raise() -> None:
    # 隔离:清 session 持久 dict
    _SESSION_CWD.pop("agent-os-v2:s1", None)
    c = MultiCwdScopeCapability(
        cwd_scope=[ResolvedCwd(path_abs="/tmp/a", label="a")],
        session_key="agent-os-v2:s1",
    )
    ts = c.get_toolset()
    tool = ts.tools["set_active_cwd"]
    res = _run(tool.function(label="unknown"))
    assert isinstance(res, str)
    assert "unknown" in res
    assert "a" in res  # 可用 label 列表含 'a'
    # 不 raise:已到此处(无异常即通过)


def test_set_active_cwd_known_label_sets_contextvar_and_session_record() -> None:
    _SESSION_CWD.pop("agent-os-v2:s1", None)
    c = MultiCwdScopeCapability(
        cwd_scope=[ResolvedCwd(path_abs="/tmp/a", label="a", default=True)],
        session_key="agent-os-v2:s1",
    )
    ts = c.get_toolset()
    tool = ts.tools["set_active_cwd"]

    # contextvar 是 context-local:tool.function 在 _run 的子 task 里 set,_active_cwd
    # 的值不传播回测试父上下文(set 仅影响当前 task 的 context 副本)。故在同一协程内
    # 验 contextvar(T4 双写之一),协程外验 session 持久 dict(跨 turn 真持久)。
    async def _call_and_check() -> str:
        res = await tool.function(label="a")
        assert _active_cwd.get() == Path("/tmp/a")  # 同 context 内已 set
        return res

    res = _run(_call_and_check())
    assert "已切换" in res
    # session 持久 dict 是跨 contextvar/跨 turn 真持久的信号(T4 _SESSION_CWD 模块级 dict)
    assert get_session_active_cwd("agent-os-v2:s1") == "/tmp/a"
