"""P8 ToolBridgeCapability — v2 ToolRegistry → pydantic-ai 具名 tool 桥接。

ADR: docs/adr/pydantic-ai-v2-adoption.md(P8 主路径退役核心前置 + P7 pitfall 解)。
ADR L27 硬要求:每个 v2 tool **独立字段**进 tools[](name+description+schema),
不再拼进 system prompt。

v2 tool 是 ``handler(**arguments)`` 动态签名,不能逐个转 pydantic-ai tool(后者要固定
函数签名作 schema)。**V1 解法**:``get_toolset`` 为 registry 每个 tool 动态注册 1 个
具名 pydantic-ai tool(name=registry name,description=registry desc),
``Tool.from_schema`` 直注 registry parameters JSON schema(强 schema,跳过 pydantic
校验,any_schema validator + 模型原样传 arguments dict)。``get_instructions`` 返空
字符串(tool 清单不再进 system prompt,模型直接看 tools[] 字段)。
模式对称 MemoryCapability 的 dispatch 思路,但每个 tool 一独立字段(ADR L27)。

**P7 pitfall 语义鸿沟解**(workflow 对抗验证发现):``tool_executor.execute`` 返
``{status:'error'}`` 是**返回值非 raise**,pydantic-ai ``on_tool_execute_error`` 只在
raise 时触发,pitfail 计数会静默失效。本 capability在每个具名 tool 的 wrapper **内**显式
处理失败状态 → 调 ``pitfall_registry.match/increment/record``,不依赖 hook 自动接管。

web 弃用后 canvas 双发不再迁移(随 P8 _node_tool 退役删);observe 由 ObserveCapability
独立覆盖。本 capability 只管 tool 执行 + pitfall 计数。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from pydantic_ai import FunctionToolset, Tool
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.toolsets import AbstractToolset

logger = logging.getLogger(__name__)


def _classify_tool_error(error_msg: str) -> str:
    """粗粒度工具错误分类(对称 chat.py 的模块级 helper;复制以解耦主路径)。

    timeout / file_not_found / permission_denied / tool_error。pitfail match 依赖
    稳定 error_type,保持简单确定性。
    """
    _msg = (error_msg or "").lower()
    if "timeout" in _msg or "timed out" in _msg:
        return "timeout"
    if "not found" in _msg or "no such file" in _msg or "filenotfound" in _msg:
        return "file_not_found"
    if "permission" in _msg or "denied" in _msg:
        return "permission_denied"
    return "tool_error"


@dataclass
class ToolBridgeCapability(AbstractCapability[Any]):
    """v2 ToolRegistry → pydantic-ai 具名 tool(每 tool 一独立字段)+ pitfall wrapper。"""

    id: str = "tool_bridge"
    description: str = "Bridge v2 ToolRegistry → per-tool named pydantic-ai Tool + pitfail counting"
    defer_loading: bool = False  # 主路径工具,必须挂(非 recall)
    tool_executor: Any = None  # src.tools.executor.ToolExecutor(None → no tools)
    pitfail_registry: Any = None  # _state.pitfail_registry(None → pitfall no-op)
    # P2(spec.tools 接通):白/黑名单过滤 registry tools。None=不过滤(全量,向后兼容)。
    # routes._build_native_session 从 spec.tools(ToolPolicy.allow/deny)透传(空 list→None);
    # 子代理/chat/workflow 实例化不传→全量。
    tool_allow: list[str] | None = None
    tool_deny: list[str] | None = None
    # P2-1: 父 turn 上下文(session_id/agent_id),per-session 稳定(主 turn chat.py 实例化
    # 注入)。透传给声明 ``_ctx`` kw 的 handler(workflow_run_handler)→ workflow run 用真
    # session_id 分组 observe 事件(替代 v2_workflow.py 的 "workflow" 常量)。其余实例化点
    # (wf 子 agent engine.py:327 / assemble routes.py:405 / agent_runner)不传 → 空 →
    # 向后兼容(workflow_run_handler 走 "workflow" 默认)。
    turn_session_id: str = ""
    turn_agent_id: str = ""

    def get_instructions(self) -> str:
        # V1:tool 清单不再进 system prompt(ADR L27 — name/desc/schema 各自独立字段
        # 进 tools[]);返回空字符串。模型直接从 tools[] 字段读 tool 清单。
        return ""

    def get_toolset(self) -> AbstractToolset[Any]:
        # 返 PrefixedToolset(prefix='v2'),tool 名变 v2_<name>。命名空间隔离:
        # 与 5B MCP 侧 build_mcp_toolsets 的 .prefixed(name) 对称,两路并入
        # pydantic-ai CombinedToolset 不再因跨 toolset 重名抛 UserError(grill blocker A)。
        # PrefixedToolset.call_tool 自动 strip v2_ 前缀再 dispatch,execute 路径零回归。
        ts = FunctionToolset[Any]()
        executor = self.tool_executor
        if executor is None or executor.registry is None:
            return ts
        tools = executor.registry.list_tools() or []
        # P2(spec.tools):allow 白名单 / deny 黑名单过滤(None=全量,向后兼容)
        names = ToolBridgeCapability._filter(
            [t["name"] for t in tools], self.tool_allow, self.tool_deny)
        by_name = {t["name"]: t for t in tools}
        for name in names:
            t = by_name[name]
            desc = t.get("description") or ""
            params = t.get("parameters") or {"type": "object", "properties": {}}
            ts.add_tool(_make_named_tool(
                executor, self.pitfail_registry, name, desc, params,
                self.turn_session_id, self.turn_agent_id))
        return ts.prefixed("v2")

    @staticmethod
    def _filter(
        names: list[str], allow: list[str] | None, deny: list[str] | None,
    ) -> list[str]:
        """P2(spec.tools):白/黑名单过滤。allow 非空→只留白名单;deny 非空→剔除;
        均 None→全量(向后兼容)。routes 侧空 list 已转 None(避免 [] 误当空白名单清空)。
        """
        out = []
        for n in names:
            if allow is not None and n not in allow:
                continue
            if deny and n in deny:
                continue
            out.append(n)
        return out

    @staticmethod
    def _record_pitfall(pitfail: Any, tool_name: str, error_msg: str) -> None:
        """P7:tool_executor 失败 → pitfail match/increment/record(wrapper 内,不依赖 hook)。

        pitfail None 或任何异常都静默降级(零回归,绝不污染 Agent.run)。
        """
        if pitfail is None:
            return
        try:
            err_type = _classify_tool_error(error_msg)
            existing = pitfail.match(tool_name, err_type)
            if existing:
                pitfail.increment_recurrence(existing[0].id)
            else:
                from src.pitfail import PitfallRecord
                pitfail.record(PitfallRecord(
                    id="", file_path=tool_name, error_type=err_type,
                    symptom=error_msg, root_cause=error_msg, fix="",
                    tags=[tool_name],
                ))
        except Exception:
            logger.warning("ToolBridge pitfall record failed (no-op)", exc_info=True)


async def _execute_via_registry(
    executor: Any, pitfail: Any, tool_name: str, arguments: dict,
    ctx_payload: dict | None = None,
) -> str:
    """execute_tool 核心逻辑(抽 module-level 便单测,不依赖 pydantic-ai Tool wrapper)。

    success → str(output);失败状态/异常 → pitfall 计数 + ``[Tool error]`` 串。

    P2-1: ``ctx_payload`` 透传给 ``executor.execute(_ctx=...)``,仅 workflow_run_handler
    等声明 ``_ctx`` kw 的 handler 接收(executor introspect 跳过其余)。
    """
    if executor is None:
        return "[Tool error] tool_executor unavailable"
    try:
        result = await executor.execute(tool_name, arguments or {}, _ctx=ctx_payload)
    except Exception as exc:
        ToolBridgeCapability._record_pitfall(pitfail, tool_name, str(exc))
        return f"[Tool error] {tool_name}: {exc}"
    status = result.get("status", "error")
    if status == "success":
        return str(result.get("output", ""))
    # P7:失败状态(error/timeout/blocked/blocked_output)→ pitfall 计数
    error_msg = result.get("error") or f"tool {status}"
    ToolBridgeCapability._record_pitfall(pitfail, tool_name, error_msg)
    return f"[Tool error] {tool_name}: {error_msg}"


def _make_named_tool(
    executor: Any, pitfail: Any, name: str, description: str, parameters: dict,
    turn_session_id: str = "", turn_agent_id: str = "",
) -> Tool[Any]:
    """V1:为 registry 单个 tool 造一个具名 pydantic-ai Tool(强 schema 路径 a)。

    ``Tool.from_schema`` 直注 registry parameters JSON schema →
    ``parameters_json_schema``(any_schema validator 跳过 pydantic 校验,
    模型原样传 arguments dict)。每 tool 一独立字段进 tools[](ADR L27)。
    wrapper dispatch 到 ``_execute_via_registry``(P7 pitfall 计数语义零回归)。

    P2-1: ``turn_session_id``/``turn_agent_id`` 从 ToolBridgeCapability 实例字段(主 turn
    chat.py 注入)闭包进 ``_wrapper`` → ``ctx_payload`` → ``executor.execute(_ctx=...)``。
    空 → ``ctx_payload=None``(向后兼容,等价改前行为)。
    """
    async def _wrapper(**arguments: Any) -> str:
        ctx_payload = None
        if turn_session_id or turn_agent_id:
            ctx_payload = {"session_id": turn_session_id, "agent_id": turn_agent_id}
        return await _execute_via_registry(executor, pitfail, name, arguments, ctx_payload)

    return Tool.from_schema(
        function=_wrapper,
        name=name,
        description=description,
        json_schema=parameters,
    )
