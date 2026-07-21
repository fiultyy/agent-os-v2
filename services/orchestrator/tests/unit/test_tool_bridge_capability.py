"""ToolBridgeCapability 单测(P8 tool 桥接 + P7 pitfall 语义鸿沟解)。

核心:v2 ToolRegistry → execute_tool dispatch(success→output / 失败→pitfall 计数)。
P7:tool_executor 返 {status:'error'} 是返回值非 raise → wrapper 内显式调 pitfall
(不依赖 on_tool_execute_error 只接 raise 的 hook)。
"""

from __future__ import annotations

import asyncio

from src.harness.capabilities.tool_bridge_capability import (
    ToolBridgeCapability,
    _classify_tool_error,
    _execute_via_registry,
)


class _StubExecutor:
    """记录 execute 调用;registry=self(list_tools 在 self)。"""

    def __init__(self, result=None, raises=None) -> None:
        self._result = result
        self._raises = raises
        self.registry = self  # capability.get_instructions 查 executor.registry.list_tools()
        self._tools = [{
            "name": "file_read", "description": "read a file",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
        }]

    def list_tools(self):  # registry.list_tools() 接口
        return self._tools

    async def execute(self, name, args):
        if self._raises:
            raise self._raises
        return self._result


class _StubPitfail:
    def __init__(self) -> None:
        self.records = []
        self._existing = {}  # (name, err_type) -> [record]
        self.increments = []

    def match(self, name, err_type):
        return self._existing.get((name, err_type))

    def increment_recurrence(self, id):
        self.increments.append(id)

    def record(self, rec):
        self.records.append(rec)
        self._existing[(rec.file_path, rec.error_type)] = [rec]


# ── dispatch:execute_tool 核心逻辑 ─────────────────────────────────────

def test_execute_success_returns_output() -> None:
    ex = _StubExecutor(result={"status": "success", "output": "hello", "error": None})
    r = asyncio.run(_execute_via_registry(ex, None, "file_read", {"path": "/x"}))
    assert r == "hello"


def test_execute_error_records_pitfall() -> None:
    pf = _StubPitfail()
    ex = _StubExecutor(result={"status": "error", "output": None, "error": "not found: /x"})
    r = asyncio.run(_execute_via_registry(ex, pf, "file_read", {"path": "/x"}))
    assert r.startswith("[Tool error]")
    assert len(pf.records) == 1
    assert pf.records[0].error_type == "file_not_found"


def test_execute_timeout_pitfall_type() -> None:
    pf = _StubPitfail()
    ex = _StubExecutor(result={"status": "timeout", "error": "timed out after 30s"})
    asyncio.run(_execute_via_registry(ex, pf, "web_search", {}))
    assert pf.records[0].error_type == "timeout"


def test_execute_exception_records_pitfall() -> None:
    pf = _StubPitfail()
    ex = _StubExecutor(raises=RuntimeError("boom"))
    r = asyncio.run(_execute_via_registry(ex, pf, "x", {}))
    assert "boom" in r
    assert len(pf.records) == 1


def test_repeat_error_increments_recurrence() -> None:
    """P7:第二次失败 match 命中已记录 → increment_recurrence(非再 record)。"""
    pf = _StubPitfail()
    ex = _StubExecutor(result={"status": "error", "error": "permission denied"})
    asyncio.run(_execute_via_registry(ex, pf, "x", {}))   # record
    asyncio.run(_execute_via_registry(ex, pf, "x", {}))   # match → increment
    assert len(pf.records) == 1
    assert len(pf.increments) == 1


# ── env gate / 不污染主路径 ───────────────────────────────────────────

def test_pitfall_none_no_op() -> None:
    ex = _StubExecutor(result={"status": "error", "error": "x"})
    r = asyncio.run(_execute_via_registry(ex, None, "x", {}))   # pitfall None 不崩
    assert r.startswith("[Tool error]")


def test_executor_none_returns_unavailable() -> None:
    r = asyncio.run(_execute_via_registry(None, None, "x", {}))
    assert "unavailable" in r


# ── instructions + toolset 披露(V1:具名 tool,instructions 空) ─────────

def test_instructions_empty_in_v1() -> None:
    """V1 ADR L27:tool 清单不再进 system prompt。"""
    cap = ToolBridgeCapability(tool_executor=_StubExecutor())
    assert cap.get_instructions() == ""


def test_instructions_empty_when_no_executor() -> None:
    assert ToolBridgeCapability(tool_executor=None).get_instructions() == ""


def test_toolset_has_named_tool() -> None:
    """V1:registry 每个 tool 一具名字段(名=registry name)。"""
    cap = ToolBridgeCapability(tool_executor=_StubExecutor())
    ts = cap.get_toolset()
    assert "file_read" in ts.tools
    assert "execute_tool" not in ts.tools   # V0 dispatch tool 已退役


def test_defer_loading_false() -> None:
    assert ToolBridgeCapability().defer_loading is False


# ── 错误分类 ──────────────────────────────────────────────────────────

def test_classify_tool_error() -> None:
    assert _classify_tool_error("timed out") == "timeout"
    assert _classify_tool_error("FileNotFoundError: x") == "file_not_found"
    assert _classify_tool_error("permission denied") == "permission_denied"
    assert _classify_tool_error("something weird") == "tool_error"
