"""CodebaseContextBuilder — L3: 代码库上下文构建。

从项目文件系统构建结构图、变更历史、测试状态等，
供 CAContextCoding.layer3 使用。
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional


class CodebaseContextBuilder:
    """L3: 代码库上下文构建器。"""

    DEFAULT_IGNORES = frozenset({
        ".git", "__pycache__", "node_modules", ".venv", "venv",
        "data", ".mypy_cache", ".pytest_cache", ".tox", "dist",
        "build", ".egg-info", ".eggs",
    })

    def __init__(self, workspace_path: str) -> None:
        self.workspace = Path(workspace_path)

    def build_project_structure(self, max_depth: int = 3) -> str:
        """构建项目结构图（树形文本）。"""
        if not self.workspace.exists():
            return "(workspace not found)"

        lines: list[str] = []
        entries = sorted(self.workspace.rglob("*"))
        for p in entries:
            if not self._should_include(p, max_depth):
                continue
            rel = p.relative_to(self.workspace)
            depth = len(rel.parts)
            prefix = "  " * depth
            if p.is_dir():
                lines.append(f"{prefix}{p.name}/")
            else:
                lines.append(f"{prefix}{p.name}")
        return "\n".join(lines) if lines else "(empty)"

    def get_change_history(self, limit: int = 10) -> list[str]:
        """获取 git 变更历史。"""
        try:
            result = subprocess.run(
                ["git", "-C", str(self.workspace), "log", "--oneline", f"-n{limit}"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip().split("\n")
            return []
        except Exception:
            return []

    def get_test_status(self) -> str:
        """获取测试状态（简化：统计测试文件）。"""
        if not self.workspace.exists():
            return "(workspace not found)"

        test_files: list[Path] = []
        for pattern in ("test_*.py", "*_test.py", "tests/**/*.py"):
            test_files.extend(self.workspace.glob(pattern))

        # Deduplicate
        unique = sorted({str(f.relative_to(self.workspace)) for f in test_files})
        if not unique:
            return "No test files found"
        return f"{len(unique)} test file(s) found"

    def _should_include(self, path: Path, max_depth: int) -> bool:
        """判断路径是否应包含在项目结构中。"""
        rel = path.relative_to(self.workspace)
        depth = len(rel.parts)
        if depth > max_depth:
            return False
        return not any(part in self.DEFAULT_IGNORES for part in path.parts)
