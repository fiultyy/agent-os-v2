"""GitContextProvider — L4: Git 上下文提供。

从 git 仓库获取 status / diff / branch / last commit，
供 CAContextCoding.layer4 使用。
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field


@dataclass
class GitContext:
    """完整的 Git 上下文快照。"""
    status: str = ""
    diff: str = ""
    branch: str = ""
    last_commit: str = ""


class GitContextProvider:
    """L4: Git 上下文提供器。"""

    def __init__(self, repo_path: str) -> None:
        self.repo = repo_path

    def get_status(self) -> str:
        """获取 git status (--porcelain)。"""
        try:
            result = subprocess.run(
                ["git", "-C", self.repo, "status", "--porcelain"],
                capture_output=True, text=True, timeout=5,
            )
            return result.stdout.strip() if result.returncode == 0 else ""
        except Exception:
            return ""

    def get_diff(self, file_path: str = "") -> str:
        """获取 git diff（可选指定文件）。"""
        try:
            cmd = ["git", "-C", self.repo, "diff"]
            if file_path:
                cmd.append(file_path)
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            return result.stdout if result.returncode == 0 else ""
        except Exception:
            return ""

    def get_branch(self) -> str:
        """获取当前分支名。"""
        try:
            result = subprocess.run(
                ["git", "-C", self.repo, "branch", "--show-current"],
                capture_output=True, text=True, timeout=5,
            )
            return result.stdout.strip() if result.returncode == 0 else ""
        except Exception:
            return ""

    def get_last_commit(self) -> str:
        """获取最近一次 commit (oneline)。"""
        try:
            result = subprocess.run(
                ["git", "-C", self.repo, "log", "--oneline", "-n1"],
                capture_output=True, text=True, timeout=5,
            )
            return result.stdout.strip() if result.returncode == 0 else ""
        except Exception:
            return ""

    def get_context(self) -> GitContext:
        """获取完整 Git 上下文快照。"""
        return GitContext(
            status=self.get_status(),
            diff=self.get_diff(),
            branch=self.get_branch(),
            last_commit=self.get_last_commit(),
        )
