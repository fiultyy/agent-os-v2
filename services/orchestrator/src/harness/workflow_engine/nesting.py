"""workflow_engine.nesting — W-P2-2 一层嵌套限制 + ContextVar 栈式回退。

复用(design §4 / §7):
- ``contextvars.ContextVar``(stdlib):per-task 承载 ``WorkflowContext`` 指针。
  ContextVar 是 task-local 非 coroutine-local;``asyncio.gather`` 并发子 task 时各
  task 在创建时刻**拷贝**当前 ContextVar 值,sibling 之间的 ``set/reset`` 不互染
  (design §7 caveat)。本设计靠**共享 Python 对象引用**保证父子共享可变状态
  (``RunUsage`` / ``Semaphore`` / ``Event`` 都是 mutable,各 task 看到同一对象),
  ContextVar 仅承载"找到父对象"的指针。
- ``dataclasses.replace``(stdlib):child_ctx 复制 parent 字段,**不 replace**
  ``total_usage`` / ``abort`` / ``budget_lock`` / ``worktree_manager`` / ``journal``
  → 同一 Python 对象引用(父子共享)。

一层限制(design §7):
- ``parent.depth >= 1`` → raise ``WorkflowNestingError``(root → child 唯一合法;
  child 内再嵌套 raise)。守恒 agent fan-out 爆炸 + budget 失控。

abort 传播(design §7):
- ``_spawn_agent`` 已在 ``WorkflowEngine.run._bounded`` 入口检查
  ``ctx.abort.is_set()`` 短路(engine.py:417-423),child_ctx 共享父 abort Event
  → 父 ``abort.set()`` 后所有 in-flight 子 workflow 短路。本模块零增量负担
  (abort 是 Event 引用,不存 ContextVar 状态)。
"""

from __future__ import annotations

import logging
import uuid
from contextvars import ContextVar
from dataclasses import replace
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .engine import WorkflowContext, WorkflowNodesSpec, WorkflowResult

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────
# ContextVar — task-local 承载 WorkflowContext 指针(design §7)
# 默认 None(顶层 root 调用首次进入时 fallback 到显式 parent_ctx 参数)。
# ─────────────────────────────────────────────────────────────────────
_WF_CTX: ContextVar[Optional["WorkflowContext"]] = ContextVar(
    "wf_ctx", default=None,
)


class WorkflowNestingError(RuntimeError):
    """嵌套深度超限(一层限制:root → child;child 内再嵌套 raise)。"""


def get_workflow_context() -> Optional["WorkflowContext"]:
    """读当前 task 的 WorkflowContext(无嵌套上下文时返 None)。

    暴露给 handler / journal 在 P2 通电后取真 session_id(替代 P0 默认 "workflow")。
    """
    return _WF_CTX.get()


# ─────────────────────────────────────────────────────────────────────
# Engine 单例(模块级,持 emitter / pitfail / tool_executor)。
# ponytail:懒建 + 单进程复用(emitter WS 连接 module-level);不做 thread-safe
# (单 asyncio loop 串行)。同 v2_workflow._build_engine 风格,但缓存实例。
# ─────────────────────────────────────────────────────────────────────
_engine_instance: Optional["WorkflowEngine"] = None  # type: ignore[name-defined]


def _get_engine() -> "WorkflowEngine":  # type: ignore[name-defined]
    """模块级 WorkflowEngine 单例(design §7)。

    从 ``src.services._state`` 注入 emitter / pitfail_registry / tool_executor;
    None-guard 全降级(emitter=None 时 _emit_workflow 静默跳过,run 不崩)。
    """
    global _engine_instance
    if _engine_instance is not None:
        return _engine_instance
    from .engine import WorkflowEngine
    try:
        from src.services import _state
        emitter = getattr(_state, "memory_observe_emitter", None)
        pitfail = getattr(_state, "pitfall_registry", None)
        tool_executor = getattr(_state, "tool_executor", None)
    except Exception:  # noqa: BLE001 — _state 不可用时降级 None deps
        logger.warning(
            "workflow_engine.nesting: _state unavailable, engine with None deps",
        )
        emitter = pitfail = tool_executor = None
    _engine_instance = WorkflowEngine(
        emitter=emitter, pitfail_registry=pitfail, tool_executor=tool_executor,
    )
    return _engine_instance


def set_engine(engine: "WorkflowEngine") -> None:  # type: ignore[name-defined]
    """测试 hook:注入 fake engine(monkeypatch 替代 _get_engine 默认单例)。"""
    global _engine_instance
    _engine_instance = engine


async def nested_run(
    spec: "WorkflowNodesSpec",
    parent_ctx: "WorkflowContext",
) -> "WorkflowResult":
    """一层嵌套限制的 workflow run(design §7 逐字实现)。

    - ``_WF_CTX.get()`` 非 None(已在 child 上下文内) → 用其作 parent(深度叠加
      检查);None(root 调用首次进入)→ fallback 显式 ``parent_ctx``。
    - ``parent.depth >= 1`` → raise ``WorkflowNestingError``(一层限制)。
    - child_ctx = ``replace(parent, depth=parent.depth+1, run_id=fresh)``:
      ``abort`` / ``budget_lock`` / ``worktree_manager`` / ``journal``
      **不 replace** → 同 Python 对象引用(父子共享 cancel / journal)。
    - ``total_usage`` 共享:**child 运行后经 ``RunUsage.incr`` 把 child 累计用量
      commit 回 parent_ctx.total_usage**(mutable 原地加,保持 design §7 caveat
      "父子 token 自然汇总到同一 budget" 语义)。engine.run 内部用
      ``ctx.total_usage = ctx.total_usage + nr.usage`` 重绑新对象(避 RK2 并发竞态),
      故需 nesting 层补这一步回 commit。
    - ``_WF_CTX.set/reset`` 包 try/finally 栈式回退(ContextVar token 语义)。
    - abort 传播:child 经 ``engine.run._bounded`` 入口的 ``ctx.abort.is_set()``
      检查(engine.py:417-423)自动短路(abort 是共享 Event 引用)。
    """
    parent = _WF_CTX.get()
    if parent is None:
        parent = parent_ctx  # root 调用首次进入(fallback 显式参数)
    if parent.depth >= 1:
        raise WorkflowNestingError(
            "nested workflow depth > 1 (one-level limit: root → child only)"
        )

    # child_ctx:depth+1 + 独立 run_id;abort / budget_lock / journal /
    # worktree_manager 同引用(共享 cancel / journal)。total_usage 同引用在
    # engine.run 期间会被重绑(fan-in ``+=`` 返新对象),回 commit 在 finally 补。
    child_ctx = replace(
        parent,
        depth=parent.depth + 1,
        run_id=f"wf_{uuid.uuid4().hex[:12]}",
        # seen / dry_counter 不 replace = 共享(嵌套 workflow 续算父的去重状态)。
    )

    token = _WF_CTX.set(child_ctx)
    try:
        engine = _get_engine()
        result = await engine.run(spec, child_ctx)
        return result
    finally:
        # 回 commit:child 经 fan-in 重绑的 total_usage 通过 mutable incr 并入
        # parent_ctx 的 total_usage(design §7 caveat:父子 token 汇入同一 budget)。
        # parent_ctx.total_usage 是 parent 上下文的 RunUsage 引用(非 child 的重绑对象)。
        try:
            parent_ctx.total_usage.incr(child_ctx.total_usage)
        except Exception:  # noqa: BLE001 — incr 失败不阻断主路径(R3 fire-and-forget)
            logger.warning(
                "workflow_engine.nesting: parent_ctx.total_usage.incr failed "
                "(child run=%s): parent budget may under-count",
                child_ctx.run_id,
                exc_info=True,
            )
        _WF_CTX.reset(token)
