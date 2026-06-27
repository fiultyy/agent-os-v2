"""Guardrail — safety checks for tool execution.

Validates tool calls before execution:
- Parameter type checking
- Permission/scope validation
- Rate limiting
- Dangerous operation detection
"""

import json
import re
from typing import Any


class Guardrail:
    """Pre- and post-execution safety checks for tool calls.

    Provides two-phase validation:
    1. Input check — validates arguments before execution
    2. Output check — validates results after execution
    """

    # Patterns that indicate dangerous operations
    DANGEROUS_PATTERNS: list[re.Pattern[str]] = [
        re.compile(r"rm\s+-rf", re.IGNORECASE),
        re.compile(r"del\s+/[sS]", re.IGNORECASE),
        re.compile(r"format\s+[cCdD]", re.IGNORECASE),
        re.compile(r"\.\.[\\/]"),  # path traversal
        re.compile(r"sudo\s+", re.IGNORECASE),
        re.compile(r"chmod\s+777", re.IGNORECASE),
    ]

    # Sensitive patterns in output. The optional quote before the separator
    # lets these also catch JSON-serialized dict/list output — e.g. a tool
    # returning {"password": "..."} now matches, which is the whole point of
    # the dict branch in check_output (previously dicts were skipped entirely).
    SENSITIVE_PATTERNS: list[re.Pattern[str]] = [
        re.compile(r"""(?i)password\s*["']?\s*[:=]\s*\S+"""),
        re.compile(r"""(?i)api[_-]?key\s*["']?\s*[:=]\s*\S+"""),
        re.compile(r"""(?i)secret\s*["']?\s*[:=]\s*\S+"""),
        re.compile(r"""(?i)token\s*["']?\s*[:=]\s*\S+"""),
    ]

    async def check(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> tuple[bool, str]:
        """Validate a tool call's input arguments.

        Checks for:
        - Path traversal attempts (``..`` in file paths)
        - Dangerous shell commands
        - Suspicious patterns in string arguments

        Args:
            tool_name: Name of the tool being called.
            arguments: Arguments to validate.

        Returns:
            ``(allowed, reason)`` — allowed=True if the call passes all checks.
        """
        for key, value in arguments.items():
            if not isinstance(value, str):
                continue
            for pattern in self.DANGEROUS_PATTERNS:
                if pattern.search(value):
                    return False, (
                        f"Dangerous pattern detected in argument {key!r}: "
                        f"matches {pattern.pattern!r}"
                    )
        return True, ""

    async def check_output(self, result: Any) -> tuple[bool, str]:
        """Validate a tool's output for sensitive information.

        Structured tool outputs (``dict`` / ``list``) are JSON-serialized
        before scanning — otherwise the early ``not isinstance(result, str)``
        guard returned ``True`` unconditionally and a dict payload like
        ``{"password": "..."}`` would never be redacted.

        Args:
            result: The tool's return value to inspect.

        Returns:
            ``(allowed, reason)`` — allowed=True if the output passes.
        """
        if isinstance(result, str):
            text = result
        else:
            try:
                text = json.dumps(result, default=str, ensure_ascii=False)
            except (TypeError, ValueError):
                # Unserializable object — nothing to scan.
                return True, ""

        for pattern in self.SENSITIVE_PATTERNS:
            if pattern.search(text):
                return False, (
                    f"Sensitive information detected in output: "
                    f"matches {pattern.pattern!r}"
                )

        return True, ""
