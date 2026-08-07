"""ToolExecutor — handles async tool call execution with closed-loop feedback.

Implements the full tool execution lifecycle (ADR-2/ADR-3):
1. fire TOOL_PRE → bus emit returns aggregated {allow,reason} decision
   (non-None + allow=False → blocked, not executed)
2. Execute with timeout; success → fire TOOL_POST(result) → emit decision
   decides blocked_output
3. timeout/exception → fire TOOL_POST_FAIL(error)

The Guardrail is no longer hard-coded here; it registers as a MemoryHook on
TOOL_PRE/TOOL_POST in engine.bootstrap(). ``bus=None`` (tests) skips all
firing — tests that don't depend on guardrail blocking stay unchanged.
"""

import asyncio
import inspect
from typing import Any

from src.memory.event_bus import EventType, MemoryEventBus
from src.memory.hooks import ToolContext
from src.tools.registry import ToolRegistry


# P2-1: 少数 handler(workflow_run_handler)声明 ``_ctx`` kw 接收父 turn 上下文
# (session_id/agent_id)。introspect 签名才透传,所有不带 ``_ctx`` 的 handler 零影响
# (向后兼容 —— executor 是通用 tool 执行器,跑 registry 全量 tool)。
# ponytail: 缓存避免每 tool call 重复 signature 反射;handler 函数对象可哈希作 key。
_accepts_ctx_cache: dict[Any, bool] = {}


def _handler_accepts_ctx(handler: Any) -> bool:
    """handler 签名是否含 ``_ctx`` 参数。缓存 + 异常降级 False(绝不阻断 tool 执行)。"""
    cached = _accepts_ctx_cache.get(handler)
    if cached is not None:
        return cached
    try:
        sig = inspect.signature(handler)
        accepts = "_ctx" in sig.parameters
    except (ValueError, TypeError):  # builtin/不可内省 → 安全降级不透传
        accepts = False
    _accepts_ctx_cache[handler] = accepts
    return accepts


class ToolExecutor:
    """Executes tool calls asynchronously with lifecycle event firing.

    Fire points (ADR-2): TOOL_PRE before run, TOOL_POST on success,
    TOOL_POST_FAIL on timeout/exception. A TOOL_PRE consumer returning
    ``{"allow": False}`` short-circuits to a ``blocked`` result; a
    TOOL_POST consumer returning ``{"allow": False}`` yields
    ``blocked_output``.
    """

    def __init__(
        self,
        registry: ToolRegistry,
        bus: MemoryEventBus | None = None,
    ) -> None:
        self._registry = registry
        self._bus = bus

    @property
    def registry(self) -> ToolRegistry:
        """Public read access to the underlying registry.

        Used by the orchestrator to enumerate tools and build native
        function-calling schemas (LLMClient.chat(tools=...)).
        """
        return self._registry

    async def _fire(self, event: EventType, ctx: ToolContext) -> Any:
        """Emit a tool lifecycle event; no-op when no bus is wired."""
        if self._bus is None:
            return None
        return await self._bus.emit(event, ctx)

    async def execute(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        timeout: float = 30.0,
        _ctx: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute a tool call and return the result.

        Flow:
        1. Look up tool in registry
        2. fire TOOL_PRE; if a consumer vetoes (allow=False) → blocked
        3. Execute with timeout
        4. fire TOOL_POST(result) on success; veto → blocked_output
        5. fire TOOL_POST_FAIL(error) on timeout/exception

        Args:
            tool_name: Name of the registered tool.
            arguments: Tool call arguments.
            timeout: Execution timeout in seconds.
            _ctx: P2-1 父 turn 上下文({session_id,agent_id,...}),仅透传给声明
                ``_ctx`` kw 的 handler(workflow_run_handler);ToolBridgeCapability 从主
                turn 实例字段注入,模型不可见(不进 tool schema)。

        Returns:
            Tool execution result with status and output.
        """
        # 1. Look up tool
        tool_entry = self._registry.get(tool_name)
        if tool_entry is None:
            return {
                "tool": tool_name,
                "status": "error",
                "output": None,
                "error": f"Tool {tool_name!r} not found in registry",
            }

        # 2. Input check via TOOL_PRE consumers (guardrail registered on bus)
        pre_ctx = ToolContext(tool_name=tool_name, arguments=dict(arguments))
        pre_decision = await self._fire(EventType.TOOL_PRE, pre_ctx)
        if isinstance(pre_decision, dict) and pre_decision.get("allow") is False:
            reason = pre_decision.get("reason", "")
            return {
                "tool": tool_name,
                "status": "blocked",
                "output": None,
                "error": f"Guardrail blocked: {reason}",
            }

        # 3. Execute with timeout
        handler = tool_entry["handler"]
        try:
            result = await asyncio.wait_for(
                self._invoke_handler(handler, arguments, _ctx),
                timeout=timeout,
            )
        except asyncio.TimeoutError as exc:
            await self._fire(
                EventType.TOOL_POST_FAIL,
                ToolContext(tool_name=tool_name, arguments=dict(arguments), error=exc),
            )
            return {
                "tool": tool_name,
                "status": "timeout",
                "output": None,
                "error": f"Tool execution timed out after {timeout}s",
            }
        except Exception as exc:
            await self._fire(
                EventType.TOOL_POST_FAIL,
                ToolContext(tool_name=tool_name, arguments=dict(arguments), error=exc),
            )
            return {
                "tool": tool_name,
                "status": "error",
                "output": None,
                "error": str(exc),
            }

        # 4. Output check via TOOL_POST consumers
        post_ctx = ToolContext(
            tool_name=tool_name, arguments=dict(arguments), result=result,
        )
        post_decision = await self._fire(EventType.TOOL_POST, post_ctx)
        if isinstance(post_decision, dict) and post_decision.get("allow") is False:
            reason = post_decision.get("reason", "")
            return {
                "tool": tool_name,
                "status": "blocked_output",
                "output": None,
                "error": f"Output guardrail: {reason}",
            }

        return {
            "tool": tool_name,
            "status": "success",
            "output": result,
            "error": None,
            "metadata": {
                "safety_deadline": True,  # Mark for safety period in forgetting
            },
        }

    async def _invoke_handler(
        self,
        handler: Any,
        arguments: dict[str, Any],
        _ctx: dict[str, Any] | None = None,
    ) -> Any:
        """Invoke a tool handler, supporting both sync and async callables.

        P2-1: handler 声明 ``_ctx`` kw 时透传父 turn 上下文,其余 handler 零影响
        (``_handler_accepts_ctx`` introspect 跳过)。_ctx 不进模型可见 arguments。
        """
        kwargs = dict(arguments)
        if _ctx is not None and _handler_accepts_ctx(handler):
            kwargs["_ctx"] = _ctx
        result = handler(**kwargs)
        if asyncio.iscoroutine(result):
            result = await result
        return result
