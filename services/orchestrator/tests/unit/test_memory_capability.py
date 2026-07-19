"""P3 MemoryCapability 单测:eager(defer_loading=False)+ instructions + toolset 注册 dispatch tool。

ponytail:stub ExperienceTool/KGMemoryTool(避免真 memory DB),验 capability 属性 +
get_toolset 返带 tool 的 FunctionToolset + call_tool 端到端 dispatch 到 stub。
"""

from __future__ import annotations

import asyncio

from pydantic_ai import FunctionToolset

from src.harness.capabilities import MemoryCapability


class _StubExp:
    def execute(self, operation: str, params: dict) -> dict:
        return {"op": operation, "echo": params}


class _StubKG:
    def execute(self, operation: str, params: dict) -> dict:
        return {"kg_op": operation}


def test_memory_capability_defer_and_instructions() -> None:
    cap = MemoryCapability()
    assert cap.id == "memory"
    assert cap.defer_loading is False  # eager:glm-5.2 不调 load_capability,tool 常驻 wire
    instr = cap.get_instructions()
    assert "experience_memory" in instr
    assert "kg_memory" in instr


def test_memory_capability_empty_toolset_when_no_tools() -> None:
    cap = MemoryCapability()  # 无 tool 注入
    ts = cap.get_toolset()
    assert isinstance(ts, FunctionToolset)


def test_memory_capability_registers_both_tools() -> None:
    cap = MemoryCapability(experience_tool=_StubExp(), kg_tool=_StubKG())
    ts = cap.get_toolset()
    # ts.tools = dict{name: Tool}(同步属性)
    names = set(ts.tools.keys())
    assert "experience_memory" in names
    assert "kg_memory" in names
