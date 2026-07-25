"""Tool registry — maps tool names to their handlers + metadata."""

from __future__ import annotations

from typing import Any, Callable


class ToolRegistry:
    """Registry of callable tools with metadata (single write: _tools dict only)."""

    def __init__(self) -> None:
        self._tools: dict[str, dict[str, Any]] = {}

    def register(
        self,
        name: str,
        handler: Callable[..., Any],
        description: str = "",
        parameters: dict[str, Any] | None = None,
    ) -> None:
        """Register a tool."""
        self._tools[name] = {
            "handler": handler,
            "description": description,
            "parameters": parameters or {},
        }

    def get(self, name: str) -> dict[str, Any] | None:
        """Get tool info by name."""
        return self._tools.get(name)

    def list_tools(self) -> list[dict[str, Any]]:
        """List all registered tools."""
        return [
            {"name": n, "description": t["description"], "parameters": t["parameters"]}
            for n, t in self._tools.items()
        ]
