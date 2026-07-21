"""workflow_engine — workflow 编码级编排内核(P0 type 定义段)。

红线(R1/R2/R5)CI grep 机械守恒(禁字面 token 出现于此 docstring,详见
docs/specs/workflow-kernel-design.md §3):
- R1:子 agent 零 memory writer capability;模块内禁调用 ingest / kg 抽取 /
  memory bus / memory service。
- R2:模块内禁 import 或改线性图原语(build_execution_graph / node_llm /
  chat.py / routes turn trigger / multi_agent_graph);workflow 经
  build_native_agent 合法。
- R5:模块内禁 import engineering-discipline capability(走 native_agent.py:114
  默认 prepend)。

本 node(W-P0-1)只落 type 定义段 + schema registry。spawn/run/emit/pipeline/loop
由后续 node(W-P0-2..W-P1-3)在同一文件追加。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field
from pydantic_ai.usage import RunUsage, UsageLimits

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
