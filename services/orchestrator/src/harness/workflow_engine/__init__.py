"""workflow_engine — workflow 编码级编排内核(包化,W-P2-1)。

P0+P1 代码迁入子模块 ``engine.py``(逐字搬迁,红线守恒 R1/R2/R5 经 grep
机械守恒);W-P2-1 拆 ``journal.py`` 子模块(跨进程 resume,SQLite WAL)。

包化对外保持完全向后兼容:``from harness.workflow_engine import X`` /
``import harness.workflow_engine as wf_mod`` 不变 — 本 ``__init__`` 把
``engine.py`` 全部公共 + 私有 helper re-export(测试内联 import 不破)。

P2 子模块:
- ``workflow_engine.engine`` — P0+P1 内核(specs / WorkflowEngine / fan-in helpers)
- ``workflow_engine.journal`` — W-P2-1 WorkflowJournal(SQLite WAL,事件溯源 resume)

红线 docstring 详见 ``engine.py``(R1/R2/R5 CI grep 机械守恒)。
"""

from .engine import (
    WORKFLOW_FLOW_EVENTS,
    LoopSpec,
    NodeResult,
    PipelineSpec,
    WorkflowContext,
    WorkflowEngine,
    WorkflowNodeSpec,
    WorkflowNodesSpec,
    WorkflowResult,
    build_native_agent,  # engine.py 顶层 ``from ..native_agent import build_native_agent``
    _extract_candidates,
    _fan_in,
    _now_ts,
    _seen_key,
    _usage_dict,
    register_schema,
    resolve_schema,
)

__all__ = [
    # P0 types
    "WORKFLOW_FLOW_EVENTS",
    "WorkflowNodeSpec",
    "WorkflowNodesSpec",
    "NodeResult",
    "WorkflowResult",
    "WorkflowContext",
    "WorkflowEngine",
    "register_schema",
    "resolve_schema",
    "build_native_agent",
    # P1 specs
    "PipelineSpec",
    "LoopSpec",
    # helpers(测试内联 import 兼容)
    "_fan_in",
    "_usage_dict",
    "_now_ts",
    "_extract_candidates",
    "_seen_key",
]
