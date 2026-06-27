"""ToolExecutor — handles async tool call execution with closed-loop feedback.

Implements the full tool execution lifecycle:
1. Validate tool call against guardrails
2. Execute tool with timeout and retry
3. Process result and feed back to orchestration graph
"""

import asyncio
from typing import Any

from src.tools.registry import ToolRegistry
from src.tools.guardrail import Guardrail


class ToolExecutor:
    """Executes tool calls asynchronously with safety checks.

    Coordinates the ToolRegistry (find tool), Guardrail (safety check),
    and execution with timeout handling.
    """

    def __init__(
        self,
        registry: ToolRegistry,
        guardrail: Guardrail | None = None,
    ) -> None:
        self._registry = registry
        self._guardrail = guardrail or Guardrail()

    @property
    def registry(self) -> ToolRegistry:
        """Public read access to the underlying registry.

        Used by the orchestrator to enumerate tools and build native
        function-calling schemas (LLMClient.chat(tools=...)).
        """
        return self._registry

    async def execute(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        """Execute a tool call and return the result.

        Flow:
        1. Look up tool in registry
        2. Run guardrail checks on arguments
        3. Execute with timeout
        4. Run guardrail checks on output
        5. Return result

        Args:
            tool_name: Name of the registered tool.
            arguments: Tool call arguments.
            timeout: Execution timeout in seconds.

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

        # 2. Input guardrail check
        allowed, reason = await self._guardrail.check(tool_name, arguments)
        if not allowed:
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
                self._invoke_handler(handler, arguments),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            return {
                "tool": tool_name,
                "status": "timeout",
                "output": None,
                "error": f"Tool execution timed out after {timeout}s",
            }
        except Exception as exc:
            return {
                "tool": tool_name,
                "status": "error",
                "output": None,
                "error": str(exc),
            }

        # 4. Output guardrail check
        output_allowed, output_reason = await self._guardrail.check_output(result)
        if not output_allowed:
            return {
                "tool": tool_name,
                "status": "blocked_output",
                "output": None,
                "error": f"Output guardrail: {output_reason}",
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
    ) -> Any:
        """Invoke a tool handler, supporting both sync and async callables."""
        result = handler(**arguments)
        if asyncio.iscoroutine(result):
            result = await result
        return result
