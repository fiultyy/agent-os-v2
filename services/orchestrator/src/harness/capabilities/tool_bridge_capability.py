"""P8 ToolBridgeCapability — v2 ToolRegistry → pydantic-ai dispatch tool 桥接。

ADR: docs/adr/pydantic-ai-v2-adoption.md(P8 主路径退役核心前置 + P7 pitfall 解)。

v2 tool 是 ``handler(**arguments)`` 动态签名,不能逐个转 pydantic-ai tool(后者要固定
函数签名作 schema)。**解法:dispatch tool**——单个 ``execute_tool(tool_name, arguments)``
桥接 ``tool_executor.execute``;``get_instructions`` 列出可用 tool(name+description+
parameters JSON schema)供模型选。模式对称 MemoryCapability 的 experience_memory
(operation, params)单入口 dispatch。

**P7 pitfall 语义鸿沟解**(workflow 对抗验证发现):``tool_executor.execute`` 返
``{status:'error'}`` 是**返回值非 raise**,pydantic-ai ``on_tool_execute_error`` 只在
raise 时触发,pitfail 计数会静默失效。本 capability 在 dispatch wrapper **内**显式处理
失败状态 → 调 ``pitfall_registry.match/increment/record``,不依赖 hook 自动接管。

web 弃用后 canvas 双发不再迁移(随 P8 _node_tool 退役删);observe 由 ObserveCapability
独立覆盖。本 capability 只管 tool 执行 + pitfall 计数。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from pydantic_ai import FunctionToolset
from pydantic_ai.capabilities import AbstractCapability

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
    """v2 ToolRegistry → pydantic-ai dispatch tool(execute_tool)+ pitfall wrapper。"""

    id: str = "tool_bridge"
    description: str = "Bridge v2 ToolRegistry → execute_tool dispatch + pitfail counting"
    defer_loading: bool = False  # 主路径工具,必须挂(非 recall)
    tool_executor: Any = None  # src.tools.executor.ToolExecutor(None → no tools)
    pitfail_registry: Any = None  # _state.pitfail_registry(None → pitfall no-op)

    def get_instructions(self) -> str:
        if self.tool_executor is None or self.tool_executor.registry is None:
            return ""
        tools = self.tool_executor.registry.list_tools()
        if not tools:
            return ""
        lines = ["Available tools — call execute_tool(tool_name, arguments):"]
        for t in tools:
            desc = t.get("description", "") or ""
            lines.append(f"- {t['name']}: {desc}")
            params = t.get("parameters")
            if params:
                lines.append(f"    params: {json.dumps(params, ensure_ascii=False)}")
        return "\n".join(lines)

    def get_toolset(self) -> FunctionToolset[Any]:
        ts = FunctionToolset[Any]()
        executor = self.tool_executor
        pitfail = self.pitfail_registry

        @ts.tool_plain
        async def execute_tool(tool_name: str, arguments: dict) -> str:
            """Execute a registered tool by name. tool_name ∈ instructions list;
            arguments per the tool's params schema. Returns tool output as string,
            or ``[Tool error] <name>: <msg>`` on failure."""
            return await _execute_via_registry(executor, pitfail, tool_name, arguments)

        return ts

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
) -> str:
    """execute_tool 核心逻辑(抽 module-level 便单测,不依赖 pydantic-ai Tool wrapper)。

    success → str(output);失败状态/异常 → pitfall 计数 + ``[Tool error]`` 串。
    """
    if executor is None:
        return "[Tool error] tool_executor unavailable"
    try:
        result = await executor.execute(tool_name, arguments or {})
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
