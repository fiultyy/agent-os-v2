"""V1 tools 独立字段单测(tool_bridge_capability)。

V1(ADR L27):registry 每个 tool 注册 1 个具名 pydantic-ai Tool
(name/desc/schema 各自独立字段进 tools[]);get_instructions 返空字符串。
pitfall 计数(P7)语义零回归:mock executor 返 error status → 仍记录。
"""

from __future__ import annotations

import asyncio
from typing import Any

from pydantic_ai.models.test import TestModel
from pydantic_ai._run_context import RunContext
from pydantic_ai.usage import RunUsage

from src.harness.capabilities.tool_bridge_capability import (
    ToolBridgeCapability,
    _make_named_tool,
)


def _ctx() -> RunContext:
    """get_tools 需要 RunContext;最小构造(TestModel + 空 usage)。"""
    return RunContext(deps=None, model=TestModel(), usage=RunUsage())


def _tool_names(ts) -> set[str]:
    """PrefixedToolset 无 .tools 同步字段 → 经 get_tools 取名(异步)。"""
    return set(asyncio.run(ts.get_tools(_ctx())).keys())


class _StubExecutor:
    """记录 execute 调用;registry=self(list_tools 在 self)。"""

    def __init__(self, result=None, raises=None, tools=None) -> None:
        self._result = result
        self._raises = raises
        self.registry = self
        self._tools = tools if tools is not None else [{
            "name": "file_read", "description": "read a file",
            "parameters": {"type": "object",
                           "properties": {"path": {"type": "string"}},
                           "required": ["path"]},
        }]

    def list_tools(self):
        return self._tools

    async def execute(self, name, args, _ctx=None):
        if self._raises:
            raise self._raises
        return self._result


class _StubPitfail:
    def __init__(self) -> None:
        self.records = []
        self._existing = {}
        self.increments = []

    def match(self, name, err_type):
        return self._existing.get((name, err_type))

    def increment_recurrence(self, id):
        self.increments.append(id)

    def record(self, rec):
        self.records.append(rec)
        self._existing[(rec.file_path, rec.error_type)] = [rec]


# ── get_instructions:V1 永远返空 ─────────────────────────────────────

def test_instructions_always_empty() -> None:
    cap = ToolBridgeCapability(tool_executor=_StubExecutor())
    assert cap.get_instructions() == ""


def test_instructions_empty_when_no_executor() -> None:
    assert ToolBridgeCapability(tool_executor=None).get_instructions() == ""


# ── get_toolset:N 个具名 tool(名=v2_<registry name>,prefixed 隔离) ────

def test_toolset_has_named_tool() -> None:
    cap = ToolBridgeCapability(tool_executor=_StubExecutor())
    ts = cap.get_toolset()
    assert "v2_file_read" in _tool_names(ts)
    assert "execute_tool" not in _tool_names(ts)   # V0 dispatch tool 已退役


def test_toolset_has_n_named_tools() -> None:
    tools = [
        {"name": "file_read", "description": "read", "parameters": {"type": "object"}},
        {"name": "web_search", "description": "search", "parameters": {"type": "object"}},
        {"name": "calc", "description": "calc", "parameters": {"type": "object"}},
    ]
    cap = ToolBridgeCapability(tool_executor=_StubExecutor(tools=tools))
    ts = cap.get_toolset()
    assert _tool_names(ts) == {"v2_file_read", "v2_web_search", "v2_calc"}


def test_toolset_empty_when_no_executor() -> None:
    ts = ToolBridgeCapability(tool_executor=None).get_toolset()
    assert _tool_names(ts) == set()


def test_toolset_empty_when_registry_empty() -> None:
    cap = ToolBridgeCapability(tool_executor=_StubExecutor(tools=[]))
    assert _tool_names(cap.get_toolset()) == set()


def test_named_tool_carries_registry_schema() -> None:
    """强 schema:parameters_json_schema = registry 原 parameters dict(prefixed 不改 schema)。"""
    cap = ToolBridgeCapability(tool_executor=_StubExecutor())
    ts = cap.get_toolset()
    tool = asyncio.run(ts.get_tools(_ctx()))["v2_file_read"]
    schema = tool.tool_def.parameters_json_schema
    assert schema["properties"]["path"]["type"] == "string"
    assert schema["required"] == ["path"]


def test_named_tool_description_independent_field() -> None:
    """ADR L27:desc 独立字段(name/desc 各自独立);prefixed 只改 name 不改 desc。"""
    tools = [{"name": "x", "description": "does the X thing",
              "parameters": {"type": "object"}}]
    cap = ToolBridgeCapability(tool_executor=_StubExecutor(tools=tools))
    tool = asyncio.run(cap.get_toolset().get_tools(_ctx()))["v2_x"]
    assert tool.tool_def.name == "v2_x"
    assert tool.tool_def.description == "does the X thing"


# ── pitfall 计数(P7 语义零回归)─────────────────────────────────────

def test_named_tool_wrapper_records_pitfall_on_error() -> None:
    pf = _StubPitfail()
    ex = _StubExecutor(result={"status": "error", "error": "not found: /x"})
    tool = _make_named_tool(ex, pf, "file_read", "read a file",
                            {"type": "object", "properties": {}})
    r = asyncio.run(tool.function(path="/x"))
    assert r.startswith("[Tool error]")
    assert len(pf.records) == 1
    assert pf.records[0].error_type == "file_not_found"


def test_named_tool_wrapper_success_returns_output() -> None:
    ex = _StubExecutor(result={"status": "success", "output": "hello"})
    tool = _make_named_tool(ex, None, "file_read", "read",
                            {"type": "object", "properties": {}})
    r = asyncio.run(tool.function(path="/x"))
    assert r == "hello"


# ── grill blocker A:v2 + MCP(同名)并入 CombinedToolset 不崩 ─────────

def test_v2_and_mcp_same_registry_name_no_conflict() -> None:
    """两路同名 registry tool(v2 + MCP 都叫 file_read)→ CombinedToolset.get_tools 不 raise。

    grill blocker A 根因:v2 名=registry 名(无 prefix)与 MCP .prefixed(name) 同名时,
    pydantic-ai CombinedToolset.get_tools 抛 UserError(combined.py:73-77)致整 turn 崩。
    fix:v2 侧 .prefixed('v2') 隔离命名空间(v2_file_read vs mcp_file_read)。
    """
    from pydantic_ai.toolsets import CombinedToolset, FunctionToolset

    # v2 路径(本 capability):registry tool 名 file_read → prefixed v2_file_read
    v2_ts = ToolBridgeCapability(
        tool_executor=_StubExecutor(tools=[{
            "name": "file_read", "description": "v2 read",
            "parameters": {"type": "object", "properties": {}},
        }]),
    ).get_toolset()

    # MCP 路径(对称 5B build_mcp_toolsets 的 .prefixed(name)):同名 file_read → mcp_file_read
    mcp_inner = FunctionToolset[Any]()
    mcp_inner.add_tool(_make_named_tool(_StubExecutor(), None, "file_read", "mcp read",
                                        {"type": "object", "properties": {}}))
    mcp_ts = mcp_inner.prefixed("mcp")

    combined = CombinedToolset([v2_ts, mcp_ts])
    # 原先会 raise UserError(name conflict 'file_read');fix 后两路 prefix 隔离 → 不 raise
    names = set(asyncio.run(combined.get_tools(_ctx())).keys())
    assert names == {"v2_file_read", "mcp_file_read"}


def test_prefixed_dispatch_uses_original_name() -> None:
    """PrefixedToolset.call_tool strip v2_ 前缀 → execute 仍收原始 registry 名(零回归)。"""
    ex = _StubExecutor(result={"status": "success", "output": "ok"})
    ts = ToolBridgeCapability(tool_executor=ex).get_toolset()
    tools = asyncio.run(ts.get_tools(_ctx()))
    tool = tools["v2_file_read"]
    # call_tool 经 PrefixedToolset strip 前缀,executor.execute 收到原始名 'file_read'
    r = asyncio.run(ts.call_tool("v2_file_read", {"path": "/x"}, _ctx(), tool))
    assert r == "ok"
