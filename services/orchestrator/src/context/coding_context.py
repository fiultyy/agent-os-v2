"""CAContextCoding — Coding Agent 场景特化的 Context Architecture 六层模型。

Implements D-19: CA Coding 场景优化
- L1: Tool Descriptions (risk_level + sideline_required)
- L2: Behavioral Rules (Coding SOUL)
- L3: Memory & Context (project structure + change history + PitFail + tests)
- L4: Environment State (git status/diff + linter + coverage)
- L5: Conversation History (dynamic, handled by ContextCompiler)
- L6: Output (diff/patch/test result formatting)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class RiskLevel(Enum):
    """Tool risk levels for L1 metadata."""
    LOW = "low"            # 读文件、搜索
    MEDIUM = "medium"      # 写文件、创建
    HIGH = "high"          # 删除、执行命令
    CRITICAL = "critical"  # 危险操作（rm -rf、DROP TABLE）


@dataclass
class ToolRiskMetadata:
    """L1: 工具风险元数据，附加到每个 tool description。"""
    risk_level: RiskLevel
    sideline_required: bool = False
    requires_confirmation: bool = False
    undoable: bool = True


@dataclass
class CodingLayer1:
    """L1: 工具层特化 — 每个 tool 含 risk_level + sideline_required。"""
    tools: list[dict[str, Any]] = field(default_factory=list)

    def compile(self) -> str:
        lines: list[str] = []
        for t in self.tools:
            name = t.get("name", "unknown")
            desc = t.get("description", "")
            risk = t.get("risk_level", "low")
            sideline = t.get("sideline_required", False)
            confirm = t.get("requires_confirmation", False)
            lines.append(
                f"- {name}: {desc}  [risk={risk}, "
                f"sideline={'yes' if sideline else 'no'}, "
                f"confirm={'yes' if confirm else 'no'}]"
            )
        return "\n".join(lines)


@dataclass
class CodingLayer2:
    """L2: 行为规则特化 — Coding SOUL。"""
    soul: str = (
        "You are a coding agent. Follow these rules:\n"
        "1. Plan before code - outline approach before writing\n"
        "2. Small commits - commit frequently with clear messages\n"
        "3. Error first - when you see an error, analyze before fixing\n"
        "4. Test driven - verify changes with tests"
    )
    guidelines: list[str] = field(default_factory=list)

    def compile(self) -> str:
        parts = [self.soul]
        for g in self.guidelines:
            parts.append(f"- {g}")
        return "\n".join(parts)


@dataclass
class CodingLayer3:
    """L3: 项目上下文特化 — 项目结构图 + 变更历史 + PitFail + 测试状态。"""
    project_structure: str = ""
    change_history: list[str] = field(default_factory=list)
    pitfail_references: list[dict[str, Any]] = field(default_factory=list)
    test_status: str = ""

    def compile(self) -> str:
        parts: list[str] = []
        if self.project_structure:
            parts.append(f"[Project Structure]\n{self.project_structure}")
        if self.change_history:
            history = "\n".join(f"  {h}" for h in self.change_history[:10])
            parts.append(f"[Recent Changes]\n{history}")
        if self.pitfail_references:
            pf = "\n".join(
                f"  - {p.get('symptom', 'unknown')}: {p.get('fix', 'N/A')}"
                for p in self.pitfail_references[:5]
            )
            parts.append(f"[Known Pitfalls]\n{pf}")
        if self.test_status:
            parts.append(f"[Test Status]\n{self.test_status}")
        return "\n\n".join(parts) if parts else "(no project context)"


@dataclass
class CodingLayer4:
    """L4: 环境状态特化 — git status/diff + linter + 测试覆盖率。"""
    git_status: str = ""
    git_diff: str = ""
    linter_output: str = ""
    test_coverage: float = 0.0
    last_commit: str = ""

    def compile(self) -> str:
        parts: list[str] = []
        if self.git_status:
            parts.append(f"[Git Status]\n{self.git_status}")
        if self.last_commit:
            parts.append(f"[Last Commit]\n{self.last_commit}")
        if self.git_diff:
            # Truncate large diffs
            diff_display = self.git_diff[:2000]
            if len(self.git_diff) > 2000:
                diff_display += "\n... (truncated)"
            parts.append(f"[Git Diff]\n{diff_display}")
        if self.linter_output:
            parts.append(f"[Linter]\n{self.linter_output}")
        if self.test_coverage > 0:
            parts.append(f"[Test Coverage] {self.test_coverage:.1f}%")
        return "\n\n".join(parts) if parts else "(no environment state)"


@dataclass
class CodingLayer6:
    """L6: 输出格式特化 — diff/patch/test result 格式化。"""

    @staticmethod
    def format_diff(diff_text: str) -> str:
        """Format a unified diff for output."""
        if not diff_text:
            return "(no changes)"
        lines = diff_text.split("\n")
        formatted: list[str] = []
        for line in lines:
            if line.startswith("+") and not line.startswith("+++"):
                formatted.append(f"🟢 {line}")
            elif line.startswith("-") and not line.startswith("---"):
                formatted.append(f"🔴 {line}")
            elif line.startswith("@@"):
                formatted.append(f"📍 {line}")
            else:
                formatted.append(f"   {line}")
        return "\n".join(formatted)

    @staticmethod
    def format_test_result(results: list[dict[str, Any]]) -> str:
        """Format test results for output."""
        if not results:
            return "(no test results)"
        parts: list[str] = []
        total = len(results)
        passed = sum(1 for r in results if r.get("status") == "passed")
        parts.append(f"Tests: {passed}/{total} passed")
        for r in results:
            icon = "✅" if r.get("status") == "passed" else "❌"
            name = r.get("name", "unknown")
            parts.append(f"  {icon} {name}")
            if r.get("error"):
                parts.append(f"     Error: {r['error']}")
        return "\n".join(parts)


@dataclass
class CAContextCoding:
    """CA 六层 Coding 特化 — 编译为完整 System Prompt。"""

    layer1: CodingLayer1 = field(default_factory=CodingLayer1)
    layer2: CodingLayer2 = field(default_factory=CodingLayer2)
    layer3: CodingLayer3 = field(default_factory=CodingLayer3)
    layer4: CodingLayer4 = field(default_factory=CodingLayer4)
    layer6: CodingLayer6 = field(default_factory=CodingLayer6)

    def compile(self) -> str:
        """编译为完整 System Prompt，按六层结构组织。"""
        sections: list[str] = []

        # L2: Behavioral Rules (coding SOUL)
        l2_text = self.layer2.compile()
        if l2_text:
            sections.append(f"=== L2: Behavioral Rules ===\n{l2_text}")

        # L1: Tool Descriptions with risk metadata
        l1_text = self.layer1.compile()
        if l1_text:
            sections.append(f"=== L1: Tool Descriptions ===\n{l1_text}")

        # L3: Project Context
        l3_text = self.layer3.compile()
        if l3_text and l3_text != "(no project context)":
            sections.append(f"=== L3: Project Context ===\n{l3_text}")

        # L4: Environment State
        l4_text = self.layer4.compile()
        if l4_text and l4_text != "(no environment state)":
            sections.append(f"=== L4: Environment State ===\n{l4_text}")

        # L5 is dynamic conversation history — handled externally by ContextCompiler
        # L6 is output formatting — used at response time, not in system prompt

        return "\n\n".join(sections)
