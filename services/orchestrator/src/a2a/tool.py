"""a2a_call 工具薄桥(ADR-2 B 工具形态:native turn 内委派)。

LLM 在 turn 内调本工具(target, message) → 经 ToolBridge ``.prefixed("v2")``
展示(运行时名 = v2_ 前缀 + 注册名)→ resolve target(catalog/registry)
→ ``LocalTransport.send``(进程内,ADR-5 零网络)→ 返响应文本。

命名约束(RK11):register 名 **必须** 是 ``a2a_call``(无 v2_ 前缀)。
``ToolBridgeCapability.get_toolset`` 已 ``.prefixed("v2")``(tool_bridge_capability.py:92),
模型可见 v2_ 前缀 + ``a2a_call``;若 register 名带 v2_ 致前缀双叠。

返回状态化 dict(对称 workflow_run_handler,R7 返值非 raise):
- 成功:``{"status": "success", "output": <响应文本 str>}``
- target 不存在:``{"status": "error", "error": "target agent not found: <id>"}``
- send 异常:``{"status": "error", "error": "..."}``
ToolExecutor.execute 包顶层 ``{"status":"success","output":<本 dict>}``;
ToolBridge ``_execute_via_registry`` 取 ``.output`` 返串。
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


# ── JSON schema(model-facing,RK7 双校验的 JSON-schema 侧;handler 无二次
# pydantic 校验 — 参数仅两 str,模型原样传即可,无需 model_validate 开销)──
A2A_CALL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "target_agent_id": {
            "type": "string",
            "description": (
                "Target sibling agent id (from a2a catalog / agents.yaml). "
                "Must be a loaded agent id; unknown ids return an error, not raise."
            ),
            "minLength": 1,
        },
        "message": {
            "type": "string",
            "description": "Message to send to the target agent (its fresh turn input).",
            "minLength": 1,
        },
    },
    "required": ["target_agent_id", "message"],
}


def _resolve_registry() -> Any:
    """Lazy read src.services._state.agent_registry (avoid import-time coupling)."""
    from src.services import _state
    return _state.agent_registry


async def a2a_call_handler(
    target_agent_id: str,
    message: str,
    *,
    transport: Any = None,
    registry: Any = None,
) -> dict[str, Any]:
    """A2A internal-mesh call tool handler (ADR-2).

    Args:
        target_agent_id: sibling agent to call.
        message: turn input for the target.
        transport / registry: keyword-only injection seams for tests. Runtime
            reads ``_state.agent_registry`` and builds a default LocalTransport.

    Returns:
        Status dict (R7 返值非 raise). ``status=="success"`` → ``output`` 是
        target 响应文本;``status=="error"`` → ``error`` 描述(pitfall 经
        ToolBridge ``_execute_via_registry`` 自动计数,target-not-found 归
        ``file_not_found``/``tool_error`` 类)。
    """
    reg = registry if registry is not None else _resolve_registry()
    if reg is None:
        return {"status": "error", "error": "a2a: agent registry unavailable"}
    # Resolve target (catalog-aware): get() 是 O(1) map 查,list_cards 兜底发现。
    # 不存在 → 状态化 err(R7),不 raise(ToolBridge pitfall 接通)。
    spec = reg.get(target_agent_id)
    if spec is None:
        return {
            "status": "error",
            "error": f"a2a: target agent not found: {target_agent_id!r}",
        }

    # Build transport. LocalTransport.__init__ 无资源占用(零网络),per-call 构造
    # 可接受;transport 注入 seam 供单测 mock send。
    from a2a.transport import LocalTransport
    tport = transport if transport is not None else LocalTransport(registry=reg)
    try:
        msg = await tport.send(target_agent_id, message)
    except Exception as exc:  # noqa: BLE001 — R7:状态化不冒泡(pitfall 经 ToolBridge)
        logger.warning("a2a_call_handler: send failed (%s): %s", target_agent_id, exc)
        return {"status": "error", "error": f"a2a send: {exc}"}

    text = msg.parts[0].text if msg.parts else ""
    return {"status": "success", "output": text}


__all__ = ["A2A_CALL_SCHEMA", "a2a_call_handler"]
