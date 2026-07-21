"""workflow_engine.worktree — W-P2-3 per-agent opt-in git worktree 隔离。

复用(design §4 / §8):
- ``subprocess`` + ``pathlib``(stdlib):``git worktree add --detach`` /
  ``git worktree remove --force``。零自研。
- ``asyncio.Semaphore(1)``(stdlib):串行化 worktree node —— ``os.chdir`` 是
  进程全局,per-agent 独占执行(代价:worktree node 失去并发;opt-in 边缘场景)。
- ``contextlib.contextmanager``(stdlib):``_chdir`` 包 ``os.chdir`` + restore。

ADR-4 显式论证(design §8):
- native in-process Agent(``build_native_agent`` 返 pydantic-ai Agent)无 harness
  client 概念,worktree 仅切 Python 进程 cwd,不创建新 ClaudeClient/ClawClient。
- ``build_native_agent`` 签名(verified ``native_agent.py:63-128``)无 ``cwd`` /
  ``deps`` 参数,``deps={'cwd':cwd}`` 不可行(judge critique)→ 唯一通道是进程级
  ``os.chdir``。

opt-in(design §8 / Q6):
- 仅当 ``WorkflowNodeSpec.isolation == 'worktree'`` 时启用(EXPENSIVE
  ~200-500ms/agent:git worktree add + 子 agent 文件操作)。默认 ``None`` 走
  session cwd(YAGNI)。串行化后 worktree 真正场景是**多 workflow 跨进程**(每
  workflow 独立进程级 cwd),非单 workflow 内并发 —— Q6 留 defer/删除接口。
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────
# _chdir — 进程级 cwd 切换上下文(design §8)
# ─────────────────────────────────────────────────────────────────────
@contextmanager
def _chdir(path: Path):
    """进程级 cwd 切换上下文。

    ``os.chdir`` 是全局,故 ``WorktreeManager._wt_semaphore(1)`` 串行化所有
    worktree node(per-agent 独占执行)。yield 后还原 prev cwd(防 worktree
    被释放后 cwd 悬空 ENOENT)。
    """
    prev = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        # ponytail:os.chdir(prev) 裸调用;若 prev 已删(worktree 边缘场景)裸 raise
        # 会污染调用链。还原失败仅 warning 不 raise(cwd 错位不致命,worktree 节点
        # 已串行退出,后续 session cwd 由更外层 session restore 兜底)。
        try:
            os.chdir(prev)
        except OSError:
            logger.warning(
                "workflow_engine.worktree._chdir: restore cwd=%s failed "
                "(worktree cleanup race?); leaving cwd=%s",
                prev, path,
            )


# ─────────────────────────────────────────────────────────────────────
# WorktreeManager — per-agent opt-in git worktree 隔离(design §8)
# ─────────────────────────────────────────────────────────────────────
class WorktreeManager:
    """per-agent opt-in git worktree 隔离。

    生命周期:
    - ``async with WorktreeManager(base) as wt:`` 包整 run(``__aexit__`` 释放所有
      未 release 的 worktree,防泄漏残留)。
    - per-agent:``await wt.acquire(agent_id, run_id)`` 拿 Path(无则 git worktree add);
      agent 完成后 ``await wt.release(agent_id)``(默认 git worktree remove --force,
      ``keep=True`` 保留供 inspect)。

    并发:``_wt_semaphore = asyncio.Semaphore(1)`` 串行化所有 acquire/agent.run/release
    块 —— ``os.chdir`` 进程全局,worktree node per-agent 独占。代价:worktree node
    失去并发(opt-in 边缘场景,design §8 关键修正)。

    ADR-4:native in-process Agent 无 harness client,worktree 仅切 Python 进程 cwd,
    不创建新 ClaudeClient/ClawClient(详见模块 docstring)。
    """

    def __init__(self, base: Path) -> None:
        self.base = Path(base)  # 主 repo root(git worktree add 的 cwd)
        # agent_id → worktree Path(acquire 写入,release/release_all 读出移除)。
        self._refs: dict[str, Path] = {}
        # os.chdir 是进程全局 → 串行化所有 worktree node(per-agent 独占执行)。
        # ponytail:Semaphore(1) 全局串行;升级路径见 design §8(多进程独立 cwd,
        # 非 Semaphore)。必须在 running loop 内建(await acquire 调用前)。
        self._wt_semaphore: Optional[asyncio.Semaphore] = None

    def _sem(self) -> asyncio.Semaphore:
        """懒建 Semaphore(避免模块导入时无 running loop,匹配 nesting._get_engine 风格)。"""
        if self._wt_semaphore is None:
            self._wt_semaphore = asyncio.Semaphore(1)
        return self._wt_semaphore

    async def acquire(self, agent_id: str, run_id: str, base_ref: str = "HEAD") -> Path:
        """分配 worktree(无则 git worktree add --detach)。

        统一路径(收敛 judge cross-design risk 三 design 各写各的):
        ``<base>/.claude/worktrees/<run_id>/<agent_id>``(``.claude/`` 已在 gitignore)。
        idempotent:``wt.exists()`` 直接复返(防重复 add)。
        """
        # 统一路径:.claude/worktrees/<run_id>/<agent_id>(design §8 verified)。
        wt = self.base / ".claude" / "worktrees" / run_id / agent_id
        if not wt.exists():
            # subprocess 同步调(git worktree add ~50-200ms,await 内阻塞 event loop
            # 可接受 —— worktree 节点已 Semaphore(1) 串行,无并发 sibling 受影响)。
            # ponytail:不上 asyncio.create_subprocess_exec(增加复杂度);worktree
            # 是 opt-in EXPENSIVE 路径,阻塞单串行点 OK。升级路径:真并行多 worktree
            # 时改 create_subprocess_exec + 移除 Semaphore(1)(design §8 Q6 caveat)。
            subprocess.run(
                ["git", "worktree", "add", "--detach", str(wt), base_ref],
                cwd=str(self.base),
                check=True,
                capture_output=True,
            )
        self._refs[agent_id] = wt
        return wt

    async def release(self, agent_id: str, keep: bool = False) -> None:
        """释放 worktree(``keep=True`` 保留供 inspect,默认 git worktree remove --force)。

        ``check=False`` 容错残留(并发释放 / worktree 已手动删不 raise)。从 ``_refs``
        移除(``__aexit__`` 不重复释放)。
        """
        wt = self._refs.pop(agent_id, None)
        if wt is None or keep:
            return
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(wt)],
            cwd=str(self.base),
            check=False,  # 容错残留(已删 / 已 release)
            capture_output=True,
        )

    async def __aenter__(self) -> "WorktreeManager":
        return self

    async def __aexit__(self, *exc) -> None:
        # 整 run cleanup token:释放所有未 release 的 worktree(防泄漏残留)。
        # ponytail:遍历 list(self._refs.keys()) —— 不拷贝会在迭代中 mutate(dict size change)。
        for agent_id in list(self._refs.keys()):
            await self.release(agent_id)
