"""v2_workflow — workflow_run 工具薄桥(W-P0-5)。

工具暴露层与 workflow_engine 内核正交(design §1.1 / §2.2):handler 首行
``WorkflowNodesSpec.model_validate`` 二次校验防 ``Tool.from_schema`` any_schema
绕过(RK7);合法薄桥调 ``WorkflowEngine.run(spec, ctx)``;返回状态化 dict。

命名约束(design §2.2 / RK11):register 名 **必须** 是 ``workflow_run``(无 v2_ 前缀),
``ToolBridgeCapability.get_toolset`` 已 ``.prefixed("v2")``(tool_bridge_capability.py:83),
模型可见 ``v2_workflow_run``;若 register 名带 v2_ 致 ``v2_v2_workflow_run``。

红线(design §3)在本模块:
- R1:零记忆 capability / 零 memory_* import(子 agent 零 memory 落库 — 由
  ``WorkflowEngine._spawn_agent`` caps 组装守);本模块仅薄桥,不组装 caps。
- R7:handler 非法返 ``{status:'error', error:'invalid nodes spec'}`` 非 raise
  (tool_executor 返回值语义,pydantic-ai ``on_tool_execute_error`` 不触发)。

ctx 构造(ponytail:P0 用模块级默认 session_id / agent_id_prefix):
- ``session_id`` / ``agent_id_prefix``:handler 签名无 request ctx,P0 默认
  ``"workflow"`` / ``"wf"``(主 agent 经 ToolBridge dispatch 无法透传 turn-level
  session_id;P2 journal 通电后从 ctx 取真 session)。
- ``concurrency`` / ``run_id``:透传 handler 参数 / 模块级 uuid4。
- emitter / pitfail / tool_executor:从 ``src.services._state`` 注入(模块级
  单例 ``_engine_for``),避免每 tool call 重建 engine(emitter WS 连接是 module-level)。
- F5 journal 通电:ctx.journal=_build_journal()(fire-and-forget 降级 None)。
- F6 worktree 接通:ctx.worktree_manager=WorktreeManager(base=Path.cwd()),整 run
  经 ``async with wt_manager`` 包(__aexit__ 兜底释放未 release 的 worktree,防泄漏)。
  修前恒 None 致 engine.py:363 ``use_worktree`` 短路,isolation='worktree' silent no-op。
  WorktreeManager.__init__ 零 I/O(仅置 base/_refs/sem=None),无 worktree node 时
  __aexit__ noop(空 _refs),opt-in 隔离场景才 git worktree add/release。
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any

from harness.workflow_engine import (
    LoopSpec,
    WorktreeManager,
    WorkflowContext,
    WorkflowEngine,
    WorkflowNodesSpec,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────
# Journal 构造(W-P2-4 通电 — F5 修:此前 handler 未传 ctx.journal 致
# _spawn_agent/_emit_workflow 的 journal 双写在生产全 no-op,journal 模块
# speculative / resume 不可用)。
# fire-and-forget(R3):db 不可用(只读 FS / 路径无写权限 / pysqlite3 缺)→
# journal=None 降级,run 不崩(对位 engine.py:522-533 start_run try/except)。
# ponytail:每次 tool call 重建 journal 是可接受开销(sqlite3.connect 是 ms
# 级 open + executescript IF NOT EXISTS 幂等),且 fire-and-forget 语义下
# 连接生命周期与 handler 一致(handler 退出 GC 即关,无显式 close 必要)。
# ─────────────────────────────────────────────────────────────────────
def _build_journal() -> Any:
    try:
        from harness.workflow_engine.journal import WorkflowJournal, _default_db_path
        return WorkflowJournal(_default_db_path())
    except Exception:  # noqa: BLE001 — R3 fire-and-forget
        logger.warning("v2_workflow: journal unavailable, resume disabled", exc_info=True)
        return None


# ─────────────────────────────────────────────────────────────────────
# JSON Schema(design §2.2.1)—— 给模型提示 minItems / minLength 等,handler 内
# WorkflowNodesSpec.model_validate 是兜底双校验(RK7)。
# ─────────────────────────────────────────────────────────────────────
WORKFLOW_RUN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["nodes"],
    "additionalProperties": False,
    "properties": {
        "nodes": {
            "type": "array",
            "minItems": 1,
            "maxItems": 4096,
            "items": {
                "type": "object",
                "required": ["prompt"],
                "additionalProperties": False,
                "properties": {
                    "prompt": {"type": "string", "minLength": 1},
                    "label": {"type": "string"},
                    "model": {"type": "string"},
                    "schema_ref": {"type": "string"},
                    "effort": {
                        "type": "string",
                        "enum": ["low", "medium", "high", "xhigh", "max"],
                    },
                    "isolation": {"type": "string", "enum": ["worktree"]},
                },
            },
        },
        "fan_in": {
            "type": "string",
            "enum": ["list", "merge"],
            "default": "list",
        },
        "timeout_per_node_ms": {
            "type": "integer",
            "minimum": 1000,
            "default": 120000,
        },
        "concurrency": {
            "type": "integer",
            "minimum": 1,
            "maximum": 64,
            "default": 8,
        },
    },
}


# ─────────────────────────────────────────────────────────────────────
# JSON Schema(design §2.2.2 — workflow_loop,P1):finder_spec 单 node(产候选)
# + max_iter / budget / schema_ref / seen_key_fn / dry_limit。handler 内
# ``LoopSpec.model_validate`` 是兜底双校验(RK7)。finder_spec 用与 RUN_SCHEMA.nodes
# 同形 node schema(逐字内联,JSON-schema 无 $ref 跨文件,工具自包含)。
# ─────────────────────────────────────────────────────────────────────
_WORKFLOW_NODE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["prompt"],
    "additionalProperties": False,
    "properties": {
        "prompt": {"type": "string", "minLength": 1},
        "label": {"type": "string"},
        "model": {"type": "string"},
        "schema_ref": {"type": "string"},
        "effort": {
            "type": "string",
            "enum": ["low", "medium", "high", "xhigh", "max"],
        },
        "isolation": {"type": "string", "enum": ["worktree"]},
    },
}

WORKFLOW_LOOP_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["finder_spec"],
    "additionalProperties": False,
    "properties": {
        "finder_spec": _WORKFLOW_NODE_SCHEMA,
        "max_iter": {"type": "integer", "minimum": 1, "maximum": 100, "default": 10},
        "budget": {
            "type": "object",
            "properties": {
                "request_limit": {"type": "integer"},
                "input_tokens_limit": {"type": "integer"},
                "output_tokens_limit": {"type": "integer"},
                "total_tokens_limit": {"type": "integer"},
            },
        },
        "schema_ref": {"type": "string"},
        "seen_key_fn": {
            "type": "string",
            "enum": ["content_hash", "label"],
            "default": "content_hash",
        },
        "dry_limit": {"type": "integer", "minimum": 1, "maximum": 5, "default": 2},
    },
}


# ─────────────────────────────────────────────────────────────────────
# Engine 构造(从 _state 注入 emitter / pitfail / tool_executor)。
# 懒 import _state:单测 import 本模块时不强引 src.services 全链(避免 sqlite3 /
# pg_store / memory 子系统副作用)。None-guard 全降级,run 不崩。
# ─────────────────────────────────────────────────────────────────────
def _build_engine() -> WorkflowEngine:
    try:
        from src.services import _state
        emitter = getattr(_state, "memory_observe_emitter", None)
        pitfail = getattr(_state, "pitfail_registry", None)
        tool_executor = getattr(_state, "tool_executor", None)
    except Exception:
        logger.warning("v2_workflow: _state unavailable, engine constructed with None deps")
        emitter = pitfail = tool_executor = None
    return WorkflowEngine(
        emitter=emitter,
        pitfail_registry=pitfail,
        tool_executor=tool_executor,
    )


async def _fire_subagent_stops(node_results: Any, *, parent_session_id: str) -> None:
    """H2 ADR-2 (4):per consumed workflow sub-agent fire ``SUBAGENT_STOP``.

    每跑完一个 fan-out node(``NodeResult.agent_id`` 是 consumed agent id),fire
    一个 ``SubagentContext``;``session_id`` 用 workflow session(子 agent 经
    engine.py:335 ``session_id=ctx.session_id`` 共用 caller session),``parent_session_id``
    同为 caller session(orchestrating turn)。bus 不可用 / 单次 emit raise 均
    fire-and-forget 跳过(对位 R5,observe 缺席或 hook 异常不影响 run 返值)。

    ponytail:per-node 串行 await 而非 gather —— emit 是 fan-out + 非阻塞契约,
    串行更可观测且 N 小(concurrency cap 默认 8);gather 收益不足其复杂度。
    """
    try:
        from src.services import _state
    except Exception:  # noqa: BLE001 — lazy import 失败等同 bus 缺席
        return
    bus = getattr(_state, "memory_event_bus", None)
    if bus is None:
        return
    from src.memory.event_bus import EventType
    from src.memory.hooks import SubagentContext

    for nr in node_results or []:
        agent_id = getattr(nr, "agent_id", "") or ""
        if not agent_id:
            continue
        try:
            await bus.emit(
                EventType.SUBAGENT_STOP,
                SubagentContext(
                    agent_id=agent_id,
                    session_id=parent_session_id,
                    parent_session_id=parent_session_id,
                ),
            )
        except Exception:  # noqa: BLE001 — R5 fire-and-forget
            logger.warning(
                "v2_workflow: SUBAGENT_STOP fire failed for agent=%s", agent_id,
                exc_info=True,
            )


async def workflow_run_handler(
    nodes: list[dict],
    fan_in: str = "list",
    timeout_per_node_ms: int = 120000,
    concurrency: int = 8,
) -> dict[str, Any]:
    """P0 入口 — workflow_run 工具薄桥。

    1. **首行** ``WorkflowNodesSpec.model_validate(...)`` 二次校验(RK7 防
       ``Tool.from_schema`` any_schema 绕过):非法返
       ``{"status": "error", "error": "invalid nodes spec"}``(**返值非 raise**,
       R7 pitfall 语义鸿沟)。
    2. 合法薄桥调 ``WorkflowEngine.run(spec, ctx)``(ctx 从 _state 构造,
       emitter/pitfail/tool_executor 注入;session_id/agent_id_prefix P0 用默认)。
    3. 返回状态化 dict(design §2.2.3):
       - 成功:``{"status": "success", "output": <WorkflowResult dict>}``
       - 失败:``{"status": "error", "output": None, "error": str}``
       ToolExecutor.execute 会把本 dict 包成顶层 ``{"status":"success",
       "output": <本 dict>}``;主 agent 经 pydantic-ai tool 返串(str(result))。

    Args:
        nodes:WorkflowNodeSpec dict 列表(每 node 至少 ``prompt: str minLength 1``)。
        fan_in:fan-in 模式,``"list"``(原样数组,默认)或 ``"merge"``(dict 字段合并)。
        timeout_per_node_ms:每 node 超时(默认 120000ms;P0 不强制,留给子 agent
            ``agent.run`` 内部 timeout)。
        concurrency:并发 cap(默认 8;Semaphore 在 ``WorkflowEngine.run`` 内构造)。
    """
    # ── RK7 二次校验(首行,逐字照 design §2.2.3)──
    try:
        spec = WorkflowNodesSpec.model_validate({
            "nodes": nodes,
            "fan_in": fan_in,
            "timeout_per_node_ms": timeout_per_node_ms,
        })
    except Exception as exc:  # noqa: BLE001 — R7:返值非 raise
        logger.warning("workflow_run_handler: invalid nodes spec: %s", exc)
        return {"status": "error", "error": "invalid nodes spec"}

    # ── ctx 构造(P0 默认 session_id / agent_id_prefix,见模块 docstring)──
    # F5:journal 通电(此前 ctx.journal 恒 None 致 W-P2-4 双写全 no-op,resume
    # 不可用)。_build_journal fire-and-forget 降级 None 时,engine 侧 no-op,run 不崩。
    # F6 worktree 接通:isolation='worktree' 真生效(此前 ctx.worktree_manager 恒 None 致
    # engine.py:363 use_worktree 短路)。base=Path.cwd()=repo root;async with 兜底释放
    # 未 release 的 worktree(__aexit__ 遍历 _refs,防泄漏残留)。
    run_id = f"wf_{uuid.uuid4().hex[:12]}"
    wt_manager = WorktreeManager(base=Path.cwd())
    ctx = WorkflowContext(
        session_id="workflow",
        agent_id_prefix="wf",
        run_id=run_id,
        concurrency=concurrency,
        journal=_build_journal(),
        worktree_manager=wt_manager,
    )

    # ── 薄桥调 engine.run(async with wt_manager 兜底释放未 release 的 worktree)──
    engine = _build_engine()
    try:
        async with wt_manager:
            result = await engine.run(spec, ctx)
    except Exception as exc:  # noqa: BLE001 — R7:engine.run 异常也状态化不冒泡
        logger.warning(
            "workflow_run_handler: engine.run failed (run=%s): %s", run_id, exc,
        )
        return {"status": "error", "output": None, "error": f"engine.run: {exc}"}

    # ── H2 ADR-2 (4):fan-out N sub-agent 跑完后,每个 consumed agent 结束 fire
    #    SUBAGENT_STOP(parent=caller session=ctx.session_id)。fire-and-forget:bus
    #    不可用(observe 未起 / R5 degradation)→ 跳过,run 不崩。
    await _fire_subagent_stops(result.node_results, parent_session_id=ctx.session_id)

    return {
        "status": result.status,
        "output": _result_to_dict(result),
    }


# ─────────────────────────────────────────────────────────────────────
# workflow_loop_handler(P1 薄桥,design §2.2.3):每轮 spawn finder 子 agent
# 产候选 → seen 去重 → dry counter / budget 早收敛 break。
# 与 workflow_run_handler 同形:首行 ``LoopSpec.model_validate`` 二次校验(RK7),
# 非法返 ``{"status":"error","error":"invalid loop spec"}``(R7 状态化非 raise);
# 合法薄桥调 ``WorkflowEngine.loop(spec, ctx)``(ctx 含 journal,复用 F5 通电)。
# ─────────────────────────────────────────────────────────────────────
async def workflow_loop_handler(
    finder_spec: dict,
    max_iter: int = 10,
    budget: dict | None = None,
    schema_ref: str | None = None,
    seen_key_fn: str = "content_hash",
    dry_limit: int = 2,
) -> dict[str, Any]:
    """P1 入口 — workflow_loop 工具薄桥(design §2.2.3)。

    1. **首行** ``LoopSpec.model_validate(...)`` 二次校验(RK7):finder_spec 非 node /
       max_iter 越界 / seen_key_fn 非 Literal → 返
       ``{"status": "error", "error": "invalid loop spec"}``(R7 返值非 raise)。
    2. 合法薄桥调 ``WorkflowEngine.loop(spec, ctx)``(ctx 从 _state 构造,journal
       复用 ``_build_journal`` F5 通电;session_id/agent_id_prefix P1 用默认)。
    3. 返回状态化 dict(同 workflow_run_handler 形状)。
    """
    # ── RK7 二次校验(首行)— budget dict 原样透传,LoopSpec 内 UsageLimits 解析 ──
    try:
        spec = LoopSpec.model_validate({
            "finder_spec": finder_spec,
            "max_iter": max_iter,
            "budget": budget,
            "schema_ref": schema_ref,
            "seen_key_fn": seen_key_fn,
            "dry_limit": dry_limit,
        })
    except Exception as exc:  # noqa: BLE001 — R7:返值非 raise
        logger.warning("workflow_loop_handler: invalid loop spec: %s", exc)
        return {"status": "error", "error": "invalid loop spec"}

    # ── ctx 构造(复用 _build_journal F5 通电 + F6 worktree 接通)──
    run_id = f"wfl_{uuid.uuid4().hex[:12]}"
    wt_manager = WorktreeManager(base=Path.cwd())
    ctx = WorkflowContext(
        session_id="workflow",
        agent_id_prefix="wf",
        run_id=run_id,
        concurrency=1,  # loop 是单 node 串行(finder 每轮一次),无 fan-out
        journal=_build_journal(),
        worktree_manager=wt_manager,
    )

    # ── 薄桥调 engine.loop(async with wt_manager 兜底释放未 release 的 worktree)──
    engine = _build_engine()
    try:
        async with wt_manager:
            result = await engine.loop(spec, ctx)
    except Exception as exc:  # noqa: BLE001 — R7:engine.loop 异常状态化不冒泡
        logger.warning(
            "workflow_loop_handler: engine.loop failed (run=%s): %s", run_id, exc,
        )
        return {"status": "error", "output": None, "error": f"engine.loop: {exc}"}

    # ── H2 ADR-2 (4):loop finder 每轮 spawn consumed sub-agent,跑完后同样
    #    fire SUBAGENT_STOP(对位 workflow_run_handler)。
    await _fire_subagent_stops(result.node_results, parent_session_id=ctx.session_id)

    return {
        "status": result.status,
        "output": _result_to_dict(result),
    }


# ─────────────────────────────────────────────────────────────────────
# WorkflowResult → JSON-safe dict(主 agent 收到的 output payload)。
# RunUsage 序列化复用 workflow_engine._usage_dict(同源,零重复实现)。
# ─────────────────────────────────────────────────────────────────────
def _result_to_dict(result: Any) -> dict[str, Any]:
    """WorkflowResult dataclass → dict(node_results 逐项展开 + fan-in 字段)。

    F2(design §11 Q2):``merged_output``(fan_in='merge' 时 success-only dict 字段合并,
    'list' 时 None)+ ``errors``(status!='success' node 的 error 字符串聚合)序列化,
    让模型 / 主 agent 收到 fan-in 结果(此前仅 logger.info → 对模型 no-op)。
    """
    from harness.workflow_engine import _usage_dict

    return {
        "run_id": result.run_id,
        "status": result.status,
        "node_count": result.node_count,
        "elapsed_ms": result.elapsed_ms,
        "total_usage": _usage_dict(result.total_usage),
        "merged_output": getattr(result, "merged_output", None),
        "errors": getattr(result, "errors", []),
        "node_results": [
            {
                "label": nr.label,
                "agent_id": nr.agent_id,
                "status": nr.status,
                "output": nr.output,
                "error": nr.error,
                "usage": _usage_dict(nr.usage),
            }
            for nr in result.node_results
        ],
    }
