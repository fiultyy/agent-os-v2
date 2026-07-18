"""P3 MemoryCapability — experience + KG tools → pydantic-ai FunctionToolset(defer_loading).

ADR: docs/adr/pydantic-ai-v2-adoption.md。把 v2 的 ExperienceTool + KGMemoryTool(各自
单入口 execute(operation, params))包成 2.0 FunctionToolset 的两个 dispatch tool,
挂 MemoryCapability(defer_loading=True)。模型按需 load_capability('memory') 才看到
这两个 tool(defer 披露:大 agent 不把 tool 定义 upfront 塞 prompt)。

recall 策略(unified/weighted/keyword/kg/semantic)在 ExperienceTool/KGMemoryTool 内部,
对 model 透明。依赖(ExperienceKG/KGQueryInterface)由调用方注入。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic_ai import FunctionToolset
from pydantic_ai.capabilities import AbstractCapability


@dataclass
class MemoryCapability(AbstractCapability[Any]):
    """workspace 经验 + 知识图谱召回(defer-able)。"""

    id: str = "memory"
    description: str = "Workspace experience + knowledge-graph recall"
    defer_loading: bool = True
    experience_tool: Any = None  # src.memory.tools.experience_tool.ExperienceTool
    kg_tool: Any = None  # src.memory.tools.kg_memory_tool.KGMemoryTool

    def get_instructions(self) -> str:
        return (
            "Memory recall.\n"
            "- experience_memory: high-reuse workspace skills/patterns "
            "(operations: get_top_experiences | get_butterfly_associations | "
            "create_skill | list_skills | summarize_experience).\n"
            "- kg_memory: entity & causal-graph queries "
            "(operations: search_entities | expand | get_neighbors | "
            "shortest_path | stats).\n"
            "Load this capability when the task benefits from past workspace context."
        )

    def get_toolset(self) -> FunctionToolset[Any]:
        ts = FunctionToolset[Any]()
        if self.experience_tool is not None:
            exp = self.experience_tool

            @ts.tool_plain
            def experience_memory(operation: str, params: dict) -> dict:
                """Query workspace experiences. operation ∈ get_top_experiences |
                get_butterfly_associations | create_skill | list_skills |
                summarize_experience; params per operation."""
                return exp.execute(operation, params)

        if self.kg_tool is not None:
            kg = self.kg_tool

            @ts.tool_plain
            def kg_memory(operation: str, params: dict) -> dict:
                """Query knowledge graph. operation ∈ search_entities | expand |
                get_neighbors | shortest_path | stats; params per operation."""
                return kg.execute(operation, params)

        return ts
