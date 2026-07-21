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
