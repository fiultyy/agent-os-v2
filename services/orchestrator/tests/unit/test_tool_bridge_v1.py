"""V1 tools 独立字段单测(tool_bridge_capability)。

V1(ADR L27):registry 每个 tool 注册 1 个具名 pydantic-ai Tool
(name/desc/schema 各自独立字段进 tools[]);get_instructions 返空字符串。
pitfall 计数(P7)语义零回归:mock executor 返 error status → 仍记录。
"""

from __future__ import annotations

import asyncio

from src.harness.capabilities.tool_bridge_capability import (
    ToolBridgeCapability,
    _make_named_tool,
)


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

    async def execute(self, name, args):
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


# ── get_toolset:N 个具名 tool(名=registry name) ─────────────────────

def test_toolset_has_named_tool() -> None:
    cap = ToolBridgeCapability(tool_executor=_StubExecutor())
    ts = cap.get_toolset()
    assert "file_read" in ts.tools
    assert "execute_tool" not in ts.tools   # V0 dispatch tool 已退役


def test_toolset_has_n_named_tools() -> None:
    tools = [
        {"name": "file_read", "description": "read", "parameters": {"type": "object"}},
        {"name": "web_search", "description": "search", "parameters": {"type": "object"}},
        {"name": "calc", "description": "calc", "parameters": {"type": "object"}},
    ]
    cap = ToolBridgeCapability(tool_executor=_StubExecutor(tools=tools))
    ts = cap.get_toolset()
    assert set(ts.tools.keys()) == {"file_read", "web_search", "calc"}


def test_toolset_empty_when_no_executor() -> None:
    ts = ToolBridgeCapability(tool_executor=None).get_toolset()
    assert ts.tools == {}


def test_toolset_empty_when_registry_empty() -> None:
    cap = ToolBridgeCapability(tool_executor=_StubExecutor(tools=[]))
    assert cap.get_toolset().tools == {}


def test_named_tool_carries_registry_schema() -> None:
    """强 schema:parameters_json_schema = registry 原 parameters dict。"""
    cap = ToolBridgeCapability(tool_executor=_StubExecutor())
    ts = cap.get_toolset()
    tool = ts.tools["file_read"]
    schema = tool.tool_def.parameters_json_schema
    assert schema["properties"]["path"]["type"] == "string"
    assert schema["required"] == ["path"]


def test_named_tool_description_independent_field() -> None:
    """ADR L27:desc 独立字段(name/desc 各自独立)。"""
    tools = [{"name": "x", "description": "does the X thing",
              "parameters": {"type": "object"}}]
    cap = ToolBridgeCapability(tool_executor=_StubExecutor(tools=tools))
    tool = cap.get_toolset().tools["x"]
    assert tool.name == "x"
    assert tool.description == "does the X thing"


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
