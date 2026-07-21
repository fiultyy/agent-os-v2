"""workflow_engine — workflow 编码级编排内核(P0)。

红线(R1/R2/R5)CI grep 机械守恒(详见 docs/specs/workflow-kernel-design.md §3):
- R1:子 agent 零 memory-writer capability;模块内零记忆落库调用。
- R2:模块内零线性图原语 import 或改动;workflow 经 ``build_native_agent`` 合法。
- R5:模块内零工程纪律 capability import(走 ``native_agent.py:114`` 默认 prepend)。

本文件:W-P0-1 type 定义段 + schema registry;W-P0-2 WorkflowEngine.__init__ +
_spawn_agent(单一 spawn chokepoint)。run/emit/pipeline/loop 由后续 node 追加。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field
from pydantic_ai.usage import RunUsage, UsageLimits

# R2 import 白名单:仅 build_native_agent(ADR-sanctioned 统一 spawn 入口)+ caps。
# (线性图原语 + 工程纪律 capability 见 docstring,不 import — grep 机械守恒)
from .capabilities import ObserveCapability, ToolBridgeCapability
from .native_agent import build_native_agent

logger = logging.getLogger(__name__)

# P2 类型仅作字符串 forward-ref(WorkflowJournal / WorktreeManager 在 P2 落地,
# 此处不 import 避免 runtime ImportError + 触发 R2 grep 噪声)


# ─────────────────────────────────────────────────────────────────────
# P0 冻结值集合(R4:observe event_type 冻结为 tick_completed,真实语义塞
# data.flow_event ∈ WORKFLOW_FLOW_EVENTS + data.flow_payload)
# ─────────────────────────────────────────────────────────────────────
WORKFLOW_FLOW_EVENTS: frozenset[str] = frozenset({
    "workflow_started",                       # run 级
    "workflow_completed",                     # run 级(status + total_usage)
    "workflow_node_started",                  # per-agent(agent_id + label + model)
    "workflow_node_completed",                # per-agent(status + output + usage)
    "workflow_pipeline_stage_started",        # P1
    "workflow_pipeline_stage_completed",      # P1
    "workflow_loop_started",                  # P1(iter=0)
    "workflow_loop_iteration",                # P1
    "workflow_loop_completed",                # P1
})


# ─────────────────────────────────────────────────────────────────────
# P0 spec / result types
# ─────────────────────────────────────────────────────────────────────
class WorkflowNodeSpec(BaseModel):
    """单 node 提示词 + 选项。prompt min_length=1 是 R6 入口兜底。"""

    prompt: str = Field(min_length=1)
    label: str = "node"
    model: Optional[str] = None
    schema_ref: Optional[str] = None
    effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    isolation: Optional[Literal["worktree"]] = None


class WorkflowNodesSpec(BaseModel):
    """run() 入口 spec。nodes min_length=1 / max_length=4096 是 RK7 双校验的 pydantic 侧。"""

    nodes: list[WorkflowNodeSpec] = Field(min_length=1, max_length=4096)
    fan_in: Literal["list", "merge"] = "list"
    timeout_per_node_ms: int = 120000


@dataclass
class NodeResult:
    """单 node spawn 结果。R7:tool_executor 返 {status:'error'} 非 raise,
    由 run() gather 后 isinstance(r, Exception) 转 status='error'。"""

    label: str
    agent_id: str
    output: Any = None
    usage: RunUsage = field(default_factory=RunUsage)
    status: Literal["success", "error", "timeout"] = "success"
    error: Optional[str] = None


@dataclass
class WorkflowResult:
    """run() 返回。total_usage = per-node RunUsage 聚合(per-node 避开共享可变引用竞态,RK2)。"""

    status: Literal["success", "error"]
    node_results: list[NodeResult]
    total_usage: RunUsage
    elapsed_ms: int
    node_count: int
    run_id: str


# ─────────────────────────────────────────────────────────────────────
# P0 WorkflowContext(P1/P2 增量字段先 None,P0 不读)
# ─────────────────────────────────────────────────────────────────────
@dataclass
class WorkflowContext:
    """run/pipeline/loop 共享上下文。total_usage 公开化(非 _shared_usage,
    修正 [0]/[2] 的封装泄露 code smell)。"""

    session_id: str
    agent_id_prefix: str
    run_id: str
    concurrency: int = 8
    budget_limits: Optional[UsageLimits] = None
    total_usage: RunUsage = field(default_factory=RunUsage)
    seen: set[str] = field(default_factory=set)
    dry_counter: int = 0
    depth: int = 0
    abort: Optional[asyncio.Event] = None
    journal: Optional["WorkflowJournal"] = None  # P2
    worktree_manager: Optional["WorktreeManager"] = None  # P2


# ─────────────────────────────────────────────────────────────────────
# P0 schema registry — 复用 pydantic-ai Agent(output_type=resolve_schema(ref))
# 默认内建 'text' passthrough 返 None(走 Agent 默认 str 输出)
# ─────────────────────────────────────────────────────────────────────
_SCHEMA_REGISTRY: dict[str, type[BaseModel]] = {}


def register_schema(name: str, model: type[BaseModel]) -> None:
    """扩展点:注册自定义 schema。default 空,模型 schema_ref 命中即用。"""
    _SCHEMA_REGISTRY[name] = model


def resolve_schema(ref: Optional[str]) -> Optional[type[BaseModel]]:
    """ref=None 或 'text' → None(Agent 走默认 str passthrough)。
    未知 ref → None(降级,不抛 — ponytail:不阻断 fan-out)。
    已注册 ref → 返对应 BaseModel 子类。
    """
    if ref is None or ref == "text":
        return None
    return _SCHEMA_REGISTRY.get(ref)


# ─────────────────────────────────────────────────────────────────────
# P0 WorkflowEngine — emitter / pitfail / tool_executor 注入式
# ─────────────────────────────────────────────────────────────────────
class WorkflowEngine:
    """workflow 编码级编排内核。

    所有 spawn 经 ``build_native_agent`` 唯一 chokepoint(R2 合法);caps 组装
    绝不挂 MemoryWriterCapability(R1 子 agent 零 memory);Agent.run 失败降级返
    NodeResult(status='error') 不 abort sibling(R3 ADR-7)。

    P0 本 node 落 ``__init__`` + ``_spawn_agent``;``run`` / ``_emit_workflow`` /
    ``pipeline`` / ``loop`` 由后续 node(W-P0-3/4, W-P1-1/3)在同一类追加。
    """

    def __init__(
        self,
        emitter: Any = None,            # ObserveEmitter(_state.memory_observe_emitter)
        pitfail_registry: Any = None,   # _state.pitfail_registry(harness 故意 typo,见 design §3 R2)
        tool_executor: Any = None,      # _state.tool_executor
    ) -> None:
        self.emitter = emitter
        self.pitfail_registry = pitfail_registry
        self.tool_executor = tool_executor

    async def _spawn_agent(
        self,
        node: WorkflowNodeSpec,
        ctx: WorkflowContext,
    ) -> NodeResult:
        """单一 spawn chokepoint:组装 native Agent + 跑一轮 ``agent.run``。

        红线(对位 agent_runner.py:42-124 母版):
        - R1:caps = [ToolBridgeCapability, ObserveCapability?]——绝不挂
          记忆写入 capability(子 agent 不沉淀记忆,fan-in 后主 agent 单点落库)。
        - R5:``build_native_agent`` 调用不传工程纪律 capability 实例 → 走
          ``native_agent.py:114-116`` 默认 prepend。
        - R6:``task_input = node.prompt or "(no task input)"``(空 messages
          被智谱/Anthropic 通道拒为 400 code 1214)。
        - R3:``agent.run`` 外层 try/except Exception → 降级返
          NodeResult(status='error', error=str(exc)) 不 abort sibling。
        - RK2:per-node RunUsage(``agent.run(usage=node_usage)``)避开共享
          可变引用并发竞态;fan-in 阶段聚合由 ``run`` 负责。
        - RK3:``agent.run`` 不传 ``usage_limits``(默认 request_limit=50 会
          在 fan-out N=8+ 提前 UsageLimitExceeded);budget 罩由 P1 ctx.budget_limits。
        """
        agent_id = f"{ctx.agent_id_prefix}_{uuid.uuid4().hex[:8]}"

        # ── caps 组装(R1:零记忆写入 capability;R5:零工程纪律 capability)──
        capabilities: list[Any] = [
            ToolBridgeCapability(
                tool_executor=self.tool_executor,
                pitfail_registry=self.pitfail_registry,
            ),
        ]
        # ObserveCapability 可选:emitter 通电才挂(子 agent run 事件 forward 到 observe)。
        if self.emitter is not None:
            capabilities.append(ObserveCapability(
                emitter=self.emitter,
                harness_id=f"wf_{agent_id[:12]}",
                session_id=ctx.session_id,
            ))

        # R5:不传工程纪律 capability 实例 → 走默认 prepend 分支(native_agent.py:114)。
        agent = build_native_agent(
            instructions="",  # P0 子 agent 走 neutral system;node.prompt 是 task input
            capabilities=capabilities,
            model_name=node.model,
        )

        # R6:空串占位(逐字照搬 agent_runner.py:116)。
        task_input = node.prompt or "(no task input)"

        # RK2:per-node RunUsage(避开共享可变引用并发 incr 无锁竞态)。
        node_usage = RunUsage()
        try:
            result = await agent.run(task_input, usage=node_usage)
        except Exception as exc:  # noqa: BLE001 — R3 降级,不 abort sibling
            logger.warning(
                "workflow_engine._spawn_agent: agent.run failed (run=%s node=%s agent=%s): %s",
                ctx.run_id, node.label, agent_id, exc,
            )
            return NodeResult(
                label=node.label,
                agent_id=agent_id,
                status="error",
                error=str(exc),
            )

        return NodeResult(
            label=node.label,
            agent_id=agent_id,
            output=result.output,
            usage=node_usage,
            status="success",
        )

    # ───────────────────────────────────────────────────────────────────
    # P0 run — fan-out(Semaphore cap)+ fan-in(gather BARRIER,单点聚合)
    # ───────────────────────────────────────────────────────────────────
    async def run(
        self,
        spec: WorkflowNodesSpec,
        ctx: WorkflowContext,
    ) -> WorkflowResult:
        """P0 fan-out + fan-in 单点聚合。

        - Semaphore(ctx.concurrency=8) cap(RK4 智谱通道并发限流兜底)。
        - ``asyncio.gather(*[_bounded(n) for n in nodes], return_exceptions=True)``
          是 BARRIER 原语(零自研):单失败不 abort sibling。
        - R7 pitfall 语义鸿沟:``gather`` 后 ``isinstance(r, Exception)`` 转
          ``NodeResult(status='error')``(tool_executor 返 {status:'error'} 非
          raise,但 ``_spawn_agent`` 的 agent.run 异常走 raise 通道)。
        - RK2 fan-in 聚合:per-node RunUsage 在 ``asyncio.Lock`` 内
          ``ctx.total_usage += r.usage``(避共享可变引用竞态)。
        - fan_in='list' 原样数组(success/error 混杂);'merge' dict 字段合并
          (success only,later-wins;error 聚合 errors list,design §11 Q2)。
        - 4 事件 emit:workflow_started / node_started×N / node_completed×N /
          workflow_completed(R4 wire tick_completed)。
        """
        import time

        loop = asyncio.get_running_loop()  # RK1:沿主 event loop,禁 run_sync/asyncio.run
        lock = asyncio.Lock()              # RK2 fan-in 聚合锁
        sem = asyncio.Semaphore(max(1, ctx.concurrency))

        self._emit_workflow(
            "workflow_started",
            ctx.run_id,
            {"node_count": len(spec.nodes), "fan_in": spec.fan_in},
            session_id=ctx.session_id,
        )

        async def _bounded(node: WorkflowNodeSpec) -> NodeResult:
            self._emit_workflow(
                "workflow_node_started",
                ctx.run_id,
                {"label": node.label, "model": node.model},
                session_id=ctx.session_id,
            )
            async with sem:
                nr = await self._spawn_agent(node, ctx)
            # RK2 fan-in 聚合(per-node usage 在 Lock 内 +=;__add__ 返新对象)
            async with lock:
                ctx.total_usage = ctx.total_usage + nr.usage
            self._emit_workflow(
                "workflow_node_completed",
                ctx.run_id,
                {
                    "label": nr.label,
                    "agent_id": nr.agent_id,
                    "status": nr.status,
                    "error": nr.error,
                },
                session_id=ctx.session_id,
            )
            return nr

        started = time.monotonic()
        raw = await asyncio.gather(
            *[_bounded(n) for n in spec.nodes],
            return_exceptions=True,  # R3 BARRIER:单失败不 abort sibling
        )
        elapsed_ms = int((time.monotonic() - started) * 1000)

        node_results: list[NodeResult] = []
        for r in raw:
            if isinstance(r, Exception):
                # R7 语义鸿沟:转 NodeResult(status='error') 不冒泡。
                node_results.append(NodeResult(
                    label="unknown",
                    agent_id="unknown",
                    status="error",
                    error=f"{type(r).__name__}: {r}",
                ))
            else:
                node_results.append(r)

        # fan-in 聚合(design §11 Q2:list 原样 / merge success-only 字段合并)
        merged_output, errors = _fan_in(node_results, spec.fan_in)
        overall_status = "error" if all(nr.status != "success" for nr in node_results) else "success"

        self._emit_workflow(
            "workflow_completed",
            ctx.run_id,
            {
                "status": overall_status,
                "node_count": len(node_results),
                "usage": _usage_dict(ctx.total_usage),
            },
            session_id=ctx.session_id,
        )
        # 标 merged_output 供 handler 暴露(经 WorkflowResult 字段不增,这里仅日志侧用)
        if spec.fan_in == "merge":
            logger.info(
                "workflow_engine.run fan_in=merge (run=%s): %d fields merged, %d errors",
                ctx.run_id,
                len(merged_output) if isinstance(merged_output, dict) else 0,
                len(errors),
            )

        return WorkflowResult(
            status=overall_status,
            node_results=node_results,
            total_usage=ctx.total_usage,
            elapsed_ms=elapsed_ms,
            node_count=len(node_results),
            run_id=ctx.run_id,
        )

    # ───────────────────────────────────────────────────────────────────
    # P0 _emit_workflow — fire-and-forget(ADR-7),wire tick_completed(R4)
    # ───────────────────────────────────────────────────────────────────
    def _emit_workflow(
        self,
        event_name: str,
        run_id: str,
        payload: dict,
        session_id: Optional[str] = None,
    ) -> None:
        """workflow_* 事件 → observe wire。

        R4 照搬 ``flow.py:98-121`` 的 ``flow_event()`` 模式:event_type 恒
        ``tick_completed``(observe EventType enum 冻结),真实语义塞
        ``data.flow_event ∈ WORKFLOW_FLOW_EVENTS`` + ``data.flow_payload``。
        绝不 import EventType 或新增枚举值。

        R3 fire-and-forget:emitter=None 跳过;整体 try/except pass(emit 本身
        fire-and-forget,这里二重兜底);同步签名(design §2.1)经
        ``asyncio.create_task`` 调度 emit,run 内 await 点不会被 emit 阻塞。
        """
        try:
            if self.emitter is None:
                return
            if event_name not in WORKFLOW_FLOW_EVENTS:
                logger.warning(
                    "workflow_engine._emit_workflow: unknown flow_event=%s (run=%s)",
                    event_name, run_id,
                )
            ev = {
                "event_id": str(uuid.uuid4()),
                "harness_type": "workflow",
                "harness_id": run_id,
                "session_id": session_id or run_id,
                "tick_id": run_id,
                "event_type": "tick_completed",  # R4:冻结
                "data": {
                    "status": "success",
                    "response": "",
                    "flow_event": event_name,    # R4:真实语义
                    "flow_payload": payload,     # R4:真实 payload
                },
                "timestamp": _now_ts(),
            }
            emit = self.emitter.emit
            # fire-and-forget:调度协程不 await,emit 内部已 try/except(emit.py:95-105)
            loop = asyncio.get_event_loop()
            loop.create_task(emit(ev))
        except Exception:  # noqa: BLE001 — R3 fire-and-forget,绝不冒泡主路径
            logger.warning(
                "workflow_engine._emit_workflow: emit scheduling failed (run=%s event=%s)",
                run_id, event_name,
                exc_info=True,
            )


# ─────────────────────────────────────────────────────────────────────
# P0 fan-in helper(design §11 Q2)+ usage 序列化 + timestamp
# ponytail:模块级函数,测试可直接 import 断言;无 class 包袱
# ─────────────────────────────────────────────────────────────────────
def _fan_in(
    node_results: list[NodeResult],
    fan_in: Literal["list", "merge"],
) -> tuple[Any, list[str]]:
    """fan_in='list' → 原样 [nr.output for nr in ...](success/error 混杂)。
    fan_in='merge' → success-only dict 字段合并(later-wins;error 聚合 errors list)。
    返 (merged_or_list, errors)。
    """
    errors = [nr.error for nr in node_results if nr.status != "success" and nr.error]
    if fan_in == "list":
        return [nr.output for nr in node_results], errors
    # merge:只合 success node 的 dict output
    merged: dict = {}
    for nr in node_results:
        if nr.status != "success":
            continue
        if isinstance(nr.output, dict):
            merged.update(nr.output)  # later-wins
    return merged, errors


def _usage_dict(usage: RunUsage) -> dict:
    """RunUsage → JSON-safe dict(emit payload 用)。"""
    return {
        "requests": getattr(usage, "requests", 0) or 0,
        "input_tokens": getattr(usage, "input_tokens", 0) or 0,
        "output_tokens": getattr(usage, "output_tokens", 0) or 0,
        "total_tokens": getattr(usage, "total_tokens", 0) or 0,
    }


def _now_ts() -> str:
    """ISO8601 UTC timestamp(照搬 events.py:_now,内联避拉 events import)。"""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
