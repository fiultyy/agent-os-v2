"""Guardrail — safety checks for tool execution.

Validates tool calls before execution:
- Parameter type checking
- Permission/scope validation
- Rate limiting
- Dangerous operation detection
"""

from typing import Any


class Guardrail:
    """Pre-execution safety checks for tool calls."""

    async def check(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> tuple[bool, str]:
        """Validate a tool call.

        Returns:
            (allowed, reason) — allowed=True if the call passes all checks.
        """
        # TODO: implement safety checks
        return True, ""
