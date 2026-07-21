"""Prompt cache_control injection for Anthropic-compatible endpoints.

Ported from hermes-agent's ``prompt_caching.py``, adapted to the P2
three-layer CompiledContext structure: instead of hermes' fixed
``system_and_3`` layout, we inject a single ``cache_control`` breakpoint
at the end of the stable static prefix
(``messages[static_count - 1]``). One breakpoint caches the entire base
prompt + tools prefix (Anthropic allows up to 4 breakpoints; one suffices
for the stable prefix).

Pure functions — no global state.
"""

import copy
from typing import Any


def build_marker(ttl: str = "5m") -> dict[str, str]:
    """Build a cache_control marker for the given TTL ('5m' or '1h')."""
    marker: dict[str, str] = {"type": "ephemeral"}
    if ttl == "1h":
        marker["ttl"] = "1h"
    return marker


def remaining_breakpoint_budget(sys_markers: int, tool_markers: int) -> int:
    """Remaining Anthropic cache_control breakpoints available for the
    messages side.

    Anthropic allows up to 4 ``cache_control`` breakpoints per request.
    Native ModelSettings already consumes ``sys_markers`` (via
    ``anthropic_cache_instructions``) + ``tool_markers`` (via
    ``anthropic_cache_tool_definitions``); what's left is what the
    messages-side ``static_count`` region may use. Mirrors claw
    ``anthropic.ts:1075-1079``.

    >>> remaining_breakpoint_budget(0, 0)
    4
    >>> remaining_breakpoint_budget(1, 1)
    2
    >>> remaining_breakpoint_budget(3, 3)
    0
    """
    return max(0, 4 - sys_markers - tool_markers)


def apply_cache_marker(msg: dict[str, Any], cache_marker: dict[str, str]) -> None:
    """Add cache_control to a single message in place, handling content formats.

    - tool role / empty content: cache_control on the message itself.
    - str content: wrap into ``[{"type":"text","text":...,"cache_control":...}]``.
    - list content: cache_control on the last block.
    """
    role = msg.get("role", "")
    content = msg.get("content")

    if role == "tool":
        msg["cache_control"] = cache_marker
        return

    if content is None or content == "":
        msg["cache_control"] = cache_marker
        return

    if isinstance(content, str):
        msg["content"] = [
            {"type": "text", "text": content, "cache_control": cache_marker},
        ]
        return

    if isinstance(content, list) and content:
        last = content[-1]
        if isinstance(last, dict):
            last["cache_control"] = cache_marker


def apply_cache_control(
    messages: list[dict[str, Any]],
    static_count: int,
    ttl: str = "5m",
) -> list[dict[str, Any]]:
    """Inject a cache_control breakpoint at the end of the static prefix.

    Places one breakpoint on ``messages[static_count - 1]`` so the entire
    static prefix (base prompt + tools) is cached. Returns a deep copy;
    ``messages`` is not mutated. If ``static_count`` is out of range the
    messages are returned unchanged (cache disabled gracefully).
    """
    out = copy.deepcopy(messages)
    if not out or static_count <= 0 or static_count > len(out):
        return out
    marker = build_marker(ttl)
    apply_cache_marker(out[static_count - 1], marker)
    return out
