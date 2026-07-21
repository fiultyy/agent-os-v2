"""workflow_engine — workflow 编码级编排内核(P0)。

红线(R1/R2/R5)CI grep 机械守恒(详见 docs/specs/workflow-kernel-design.md §3):
- R1:子 agent 零 memory-writer capability;模块内零记忆落库调用。
- R2:模块内零线性图原语 import 或改动;workflow 经 ``build_native_agent`` 合法。
- R5:模块内零工程纪律 capability import(走 ``native_agent.py:114`` 默认 prepend)。

本文件:W-P0-1 type 定义段 + schema registry;W-P0-2 WorkflowEngine.__init__ +
_spawn_agent(单一 spawn chokepoint)。W-P0-3 run / W-P0-4 _emit_workflow;
W-P1-1 pipeline(无 barrier 阶段链);loop 由后续 node 追加。
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


class PipelineSpec(BaseModel):
    """pipeline() 入口 spec(W-P1-1):N 个 stage 串联,M 个 item 并发流过。

    每 item 独立跑完所有 stage(stage[i] 输入 = item / stage[i-1] 输出),
    wall-clock ≈ 单 item 链长(N × 单 stage),非 sum(M × N)(item 在不同 stage
    重叠)。stage prompt + 上一阶段 output 拼接成子 agent task input(R6 空串
    占位仍由 _spawn_agent 兜底)。
    """

    stages: list[WorkflowNodeSpec] = Field(min_length=1, max_length=64)
    items: list[str] = Field(min_length=1, max_length=4096)
    concurrency: int = Field(default=8, ge=1, le=64)


@dataclass
class NodeResult:
    """单 node spawn 结果。R7:tool_executor 返 {status:'error'} 非 raise,
    由 run() gather 后 isinstance(r, Exception) 转 status='error'。

    W-P1-2:``status='skipped'`` 标 budget 短路 / cooperative-cancel 未 spawn 的
    node(与真实 'error' 区分;_fan_in 仍按 ``status != 'success'`` 聚合)。
    """

    label: str
    agent_id: str
    output: Any = None
    usage: RunUsage = field(default_factory=RunUsage)
    status: Literal["success", "error", "timeout", "skipped"] = "success"
    error: Optional[str] = None


@dataclass
class WorkflowResult:
    """run() 返回。total_usage = per-node RunUsage 聚合(per-node 避开共享可变引用竞态,RK2)。

    budget_exceeded(W-P1-2):budget 短路触达(至少 1 个 node 因 ``check_before_request``
    超限被 short-circuit)时 True。由 ``ctx.budget_tripped`` 显式标记(与 ``ctx.abort``
    的外部主动 abort 正交 — 外部 abort 不污染此信号)。
    """

    status: Literal["success", "error"]
    node_results: list[NodeResult]
    total_usage: RunUsage
    elapsed_ms: int
    node_count: int
    run_id: str
    budget_exceeded: bool = False


# ─────────────────────────────────────────────────────────────────────
# P0 WorkflowContext(P1/P2 增量字段先 None,P0 不读)
# ─────────────────────────────────────────────────────────────────────
@dataclass
class WorkflowContext:
    """run/pipeline/loop 共享上下文。total_usage 公开化(非 _shared_usage,
    修正 [0]/[2] 的封装泄露 code smell)。

    W-P1-2 budget 并发根治:``agent.run`` 有 await 点,concurrency>1 时多 sibling
    同时读 stale ``total_usage`` → check 全过 → budget 被绕过。根治:``_spawn_agent``
    在 ``budget_lock`` 内整 lifecycle 串行(check + caps + run + fan-in commit),
    下个 sibling 入锁时 ``total_usage`` 已含前 node 实际 usage,check 不再读 stale。
    代价:budget 路径 effective concurrency=1。``budget_tripped`` 是 budget 触达
    显式标记(与 ``abort`` 正交 — 外部主动 abort 不污染 budget_exceeded 信号)。
    """

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
    budget_tripped: bool = False               # budget 触达显式标记(与 abort 正交)
    budget_lock: Optional[asyncio.Lock] = None # budget 串行互斥锁
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
        - RK3:``agent.run(usage_limits=UsageLimits(request_limit=None))`` 显式
          unset request_limit(默认 50 会在 fan-out N=8+ 提前 UsageLimitExceeded);
          budget 罩由 P1 ctx.budget_limits(check_before_request 短路)。
        - W-P1-2 budget 并发根治(skeptic finding #1/#2):``agent.run`` 有 await 点,
          concurrency>1 时多 sibling 同时读 stale ``ctx.total_usage`` → check 全过 →
          budget 被绕过(N=8/concurrency=8/budget=100/per_call=60 实测 success=8/
          total=480/budget_exceeded=False)。根治(skeptic approve 路径 1:「check 在
          fan-in 锁内同步进行」):``ctx.budget_limits`` 非 None 且有 token limit 时,
          **整个 check + caps 组装 + agent.run + fan-in commit 在 ``ctx.budget_lock``
          内串行**(每 node 完整 lifecycle 占锁)。锁释放点 = run 完 fan-in 后;因此
          sibling 入锁时 total_usage 已含前一个 node 的实际 usage,check 不再读 stale。
          触达 ``UsageLimitExceeded`` → 返 NodeResult(status='skipped',
          error='budget_exceeded') 不 spawn,设 ``ctx.budget_tripped=True`` +
          ``ctx.abort.set()`` 广播停剩余 sibling。
          代价:budget 路径下 effective concurrency=1(per-node 串行)。无 budget 时
          锁不参与,Semaphore 原并发不变。升级路径(保并发):per-spawn token estimate
          预留 + run 后 reconcile(需 estimate;当前不知,defer)。
        """
        agent_id = f"{ctx.agent_id_prefix}_{uuid.uuid4().hex[:8]}"

        # ── W-P1-2 budget 串行根治:budget 路径下整 spawn+run+fan-in 占锁 ──
        has_budget = (
            ctx.budget_limits is not None and ctx.budget_limits.has_token_limits()
        )
        # ponytail:budget 路径 effective concurrency=1(per-node 串行);升级路径
        # 见 docstring(per-spawn estimate 预留)。
        if has_budget and ctx.budget_lock is not None:
            await ctx.budget_lock.acquire()
        try:
            if has_budget:
                try:
                    ctx.budget_limits.check_before_request(ctx.total_usage)
                except Exception as exc:  # UsageLimitExceeded;nox BLE001 兜底(子类)
                    logger.info(
                        "workflow_engine._spawn_agent: budget exceeded "
                        "(run=%s node=%s total_tokens=%d): %s",
                        ctx.run_id, node.label,
                        ctx.total_usage.total_tokens, exc,
                    )
                    ctx.budget_tripped = True
                    if ctx.abort is not None:
                        ctx.abort.set()  # 广播:sibling _bounded 入口 cooperative-cancel
                    return NodeResult(
                        label=node.label,
                        agent_id=agent_id,
                        status="skipped",
                        error="budget_exceeded",
                    )
            nr = await self._spawn_agent_inner(node, ctx, agent_id)
            # fan-in commit(per-node usage → total_usage)。budget 路径在锁内
            # atomic(下个 sibling 入锁看到更新后的 total_usage);无 budget 路径
            # lock 可能未建,直接提交(_bounded 无并发竞争因总 usage 不读回)。
            ctx.total_usage = ctx.total_usage + nr.usage
            return nr
        finally:
            if has_budget and ctx.budget_lock is not None:
                ctx.budget_lock.release()

    async def _spawn_agent_inner(
        self,
        node: WorkflowNodeSpec,
        ctx: WorkflowContext,
        agent_id: str,
    ) -> NodeResult:
        """caps 组装 + agent.run(R1/R5/R6/R3/RK3 纪律;budget 串行锁外提取)。"""

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
        # RK3:显式 unset request_limit(默认 50 在 fan-out N=8+ 提前 UsageLimitExceeded)。
        # 子任务 budget 罩由 ctx.budget_limits(check_before_request 短路),父 limit 独立。
        node_usage_limits = UsageLimits(request_limit=None)
        try:
            result = await agent.run(
                task_input, usage=node_usage, usage_limits=node_usage_limits,
            )
        except Exception as exc:  # noqa: BLE001 — R3 降级,不 abort sibling
            logger.warning(
                "workflow_engine._spawn_agent: agent.run failed (run=%s node=%s agent=%s): %s",
                ctx.run_id, node.label, agent_id, exc,
            )
            # error node 不计 usage(对位 agent_runner 母版;design verify 断言
            # total_usage 仅 success node 累计)— 返默认 RunUsage(),fan-in commit 加 0。
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
        - RK2 fan-in 聚合:per-node RunUsage commit 在 ``_spawn_agent`` 内
          (budget 路径在 ``budget_lock`` 内 atomic,根治 concurrency 读-后-写竞态;
          无 budget 路径在 ``_spawn_agent`` 末尾直接 commit)。
          ``run`` 只负责 gather BARRIER + overall status / fan_in merge。
        - fan_in='list' 原样数组(success/error 混杂);'merge' dict 字段合并
          (success only,later-wins;error 聚合 errors list,design §11 Q2)。
        - 4 事件 emit:workflow_started / node_started×N / node_completed×N /
          workflow_completed(R4 wire tick_completed)。
        """
        import time

        # RK1:沿主 event loop(asyncio.Lock/Semaphore/Event 需 running loop;
        # 禁 run_sync/asyncio.run 重起 loop — 见单测 sync wrapper 模式)。
        sem = asyncio.Semaphore(max(1, ctx.concurrency))
        # W-P1-2:budget 短路 cooperative-cancel Event + budget 串行锁。budget 触达
        # 由 _spawn_agent check_before_request 设 budget_tripped + abort。
        if ctx.abort is None:
            ctx.abort = asyncio.Event()
        if ctx.budget_lock is None:
            ctx.budget_lock = asyncio.Lock()

        self._emit_workflow(
            "workflow_started",
            ctx.run_id,
            {"node_count": len(spec.nodes), "fan_in": spec.fan_in},
            session_id=ctx.session_id,
        )

        async def _bounded(node: WorkflowNodeSpec) -> NodeResult:
            # W-P1-2 cooperative-cancel:budget 触达后 sibling 入口短路,不再 spawn。
            if ctx.abort is not None and ctx.abort.is_set():
                return NodeResult(
                    label=node.label,
                    agent_id=f"{ctx.agent_id_prefix}_skip_{uuid.uuid4().hex[:6]}",
                    status="skipped",
                    error="budget_exceeded_skipped",
                )
            self._emit_workflow(
                "workflow_node_started",
                ctx.run_id,
                {"label": node.label, "model": node.model},
                session_id=ctx.session_id,
            )
            async with sem:
                nr = await self._spawn_agent(node, ctx)
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
            # W-P1-2:budget_tripped 显式标记(与 ctx.abort 外部 abort 正交)。
            budget_exceeded=ctx.budget_tripped,
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
            # fire-and-forget:调度协程不 await,emit 内部已 try/except(emit.py:95-105)。
            # 优先 get_running_loop(异步路径内更稳);RuntimeError 时(同步直调场景,
            # 如单测 / 主 agent 非协程上下文)回退 get_event_loop 取/建 loop。
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = asyncio.get_event_loop()
            loop.create_task(emit(ev))
        except Exception:  # noqa: BLE001 — R3 fire-and-forget,绝不冒泡主路径
            logger.warning(
                "workflow_engine._emit_workflow: emit scheduling failed (run=%s event=%s)",
                run_id, event_name,
                exc_info=True,
            )

    # ───────────────────────────────────────────────────────────────────
    # P1 pipeline — 无 barrier 阶段链(asyncio.Queue per stage 串联)
    # ───────────────────────────────────────────────────────────────────
    async def pipeline(
        self,
        spec: "PipelineSpec",
        ctx: WorkflowContext,
    ) -> list[Any]:
        """无 barrier 阶段链:每 item 独立跑完所有 stage,wall-clock ≈ 单 item 链长。

        模型:N 个 stage 串联,M 个 item 并发流过。stage[i] worker 从 in_q 取 item,
        跑 _spawn_agent(stage prompt + prev output 拼接成 task input),output 入
        out_q;item 在不同 stage 重叠 → wall-clock = N × 单 stage(非 M × N sum)。

        - 闭包 bug 修正:``for stage in stages: async def _stage_worker(stage=stage)``
          默认参数绑定循环变量(避免所有 worker 共享末 stage)。
        - asyncio.Queue per stage 串联 + Semaphore(concurrency)cap per stage。
        - poison pill(哨兵 None)传播:前 stage 处理完所有 item 后向 out_q 投放
          None,下游 worker 收到 None 即向前传并退出。
        - _spawn_agent 失败 / output 非 str:短路透传(本 item 后续 stage 不再 spawn,
          output 保留为前值)——ponytail:不重试,error 经 NodeResult.status 透出。
        - 事件 emit:workflow_pipeline_stage_started/completed per (stage × item),
          wire tick_completed(R4)。R1/R2/R5 经 _spawn_agent 零增量负担。
        """
        stages = spec.stages
        n_stages = len(stages)
        # N+1 queues:queue[0] = input(queue[0..N-1] 为 stage i 的 in_q),
        # queue[N] = sink(终态)。stage i 读 queue[i],写 queue[i+1]。
        queues: list[asyncio.Queue] = [asyncio.Queue() for _ in range(n_stages + 1)]
        sem = asyncio.Semaphore(max(1, spec.concurrency))
        # W-P1-2:budget 预留锁复用 run 约定(若 ctx 未带则本地建)。
        if ctx.budget_lock is None:
            ctx.budget_lock = asyncio.Lock()

        # 投放 M 个初始 item 到 queue[0]
        for item_idx, item in enumerate(spec.items):
            await queues[0].put((item_idx, item))

        async def _stage_worker(
            stage_idx: int,
            stage: WorkflowNodeSpec,  # 默认参数绑定(闭包 bug 修正)
            in_q: asyncio.Queue,
            out_q: asyncio.Queue,
        ) -> None:
            """stage worker:从 in_q 取 item,跑 _spawn_agent,output 入 out_q。

            收到 poison pill(None)→ 向 out_q 传 None + 退出(下游连锁停止)。
            """
            while True:
                msg = await in_q.get()
                try:
                    if msg is None:
                        # poison pill 传播
                        await out_q.put(None)
                        return
                    item_idx, current = msg
                    # stage prompt + 上一阶段 output(若有)拼接成 task input。
                    task_input = (
                        stage.prompt if current == "" or stage_idx == 0
                        else f"{stage.prompt}\n[prev_output]\n{current}"
                    )
                    self._emit_workflow(
                        "workflow_pipeline_stage_started",
                        ctx.run_id,
                        {
                            "stage_idx": stage_idx,
                            "stage_label": stage.label,
                            "item_idx": item_idx,
                        },
                        session_id=ctx.session_id,
                    )
                    node = WorkflowNodeSpec(
                        prompt=task_input,
                        label=f"{stage.label}_i{item_idx}_s{stage_idx}",
                        model=stage.model,
                        schema_ref=stage.schema_ref,
                        effort=stage.effort,
                        isolation=stage.isolation,
                    )
                    async with sem:
                        nr = await self._spawn_agent(node, ctx)
                    self._emit_workflow(
                        "workflow_pipeline_stage_completed",
                        ctx.run_id,
                        {
                            "stage_idx": stage_idx,
                            "stage_label": stage.label,
                            "item_idx": item_idx,
                            "status": nr.status,
                            "error": nr.error,
                        },
                        session_id=ctx.session_id,
                    )
                    # 透传:success → output 作为下一 stage 输入;error → 短路保留
                    next_val = nr.output if nr.status == "success" else current
                    await out_q.put((item_idx, next_val))
                finally:
                    in_q.task_done()

        # 启动 N 个 stage worker(stage_idx, stage 默认参数绑定避闭包 bug)
        workers = [
            asyncio.create_task(_stage_worker(stage_idx, stage, queues[stage_idx], queues[stage_idx + 1]))
            for stage_idx, stage in enumerate(stages)
        ]
        # 主协程等所有 M item 到达 sink(queue[N])后,投 N 个 poison pill 触发连锁停止。
        # queue[N] 不 spawn worker(它是 sink);主协程收集 M 个 item + 之后投 poison pills。
        results: dict[int, Any] = {}
        sink = queues[n_stages]
        for _ in range(len(spec.items)):
            item_idx, output = await sink.get()
            results[item_idx] = output
            sink.task_done()

        # 所有 item 出 sink,投 N 个 poison pill 到 queue[0] 触发 stage[0] worker 停止,
        # 它会向 queue[1] 传 None,连锁停所有 worker。
        # ponytail:poison pill 数量 = 1(单 worker per stage);worker 收到即停。
        await queues[0].put(None)
        await asyncio.gather(*workers, return_exceptions=True)

        # 按 item_idx 顺序返(进队列序 == 出 sink 序)
        return [results[i] for i in range(len(spec.items))]


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
