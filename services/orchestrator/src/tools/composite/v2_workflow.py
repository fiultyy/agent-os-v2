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
  session_id;P2 journal 通电后从 _WF_CTX 取真 session,见 design §7)。
- ``concurrency`` / ``run_id``:透传 handler 参数 / 模块级 uuid4。
- emitter / pitfail / tool_executor:从 ``src.services._state`` 注入(模块级
  单例 ``_engine_for``),避免每 tool call 重建 engine(emitter WS 连接是 module-level)。
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from harness.workflow_engine import (
    WorkflowContext,
    WorkflowEngine,
    WorkflowNodesSpec,
)

logger = logging.getLogger(__name__)


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
    run_id = f"wf_{uuid.uuid4().hex[:12]}"
    ctx = WorkflowContext(
        session_id="workflow",
        agent_id_prefix="wf",
        run_id=run_id,
        concurrency=concurrency,
    )

    # ── 薄桥调 engine.run ──
    engine = _build_engine()
    try:
        result = await engine.run(spec, ctx)
    except Exception as exc:  # noqa: BLE001 — R7:engine.run 异常也状态化不冒泡
        logger.warning(
            "workflow_run_handler: engine.run failed (run=%s): %s", run_id, exc,
        )
        return {"status": "error", "output": None, "error": f"engine.run: {exc}"}

    return {
        "status": result.status,
        "output": _result_to_dict(result),
    }


# ─────────────────────────────────────────────────────────────────────
# WorkflowResult → JSON-safe dict(主 agent 收到的 output payload)。
# RunUsage 序列化复用 workflow_engine._usage_dict(同源,零重复实现)。
# ─────────────────────────────────────────────────────────────────────
def _result_to_dict(result: Any) -> dict[str, Any]:
    """WorkflowResult dataclass → dict(node_results 逐项展开)。"""
    from harness.workflow_engine import _usage_dict

    return {
        "run_id": result.run_id,
        "status": result.status,
        "node_count": result.node_count,
        "elapsed_ms": result.elapsed_ms,
        "total_usage": _usage_dict(result.total_usage),
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
