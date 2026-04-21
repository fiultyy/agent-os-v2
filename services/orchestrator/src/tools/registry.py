"""Tool registry — maintains a catalog of available tools."""

from __future__ import annotations

from typing import Any, Callable

from src.tools.catalog import ToolCatalog, ToolCatalogEntry, ToolLayer


class ToolRegistry:
    """Registry of callable tools with metadata.

    Maintains both a handler map (for execution) and a ToolCatalog (L3.4)
    for indexing / discovery.
    """

    def __init__(self) -> None:
        self._tools: dict[str, dict[str, Any]] = {}
        self.catalog = ToolCatalog()

    def register(
        self,
        name: str,
        handler: Callable[..., Any],
        description: str = "",
        parameters: dict[str, Any] | None = None,
        *,
        layer: ToolLayer = ToolLayer.SKILL,
        category: str = "general",
        tags: list[str] | None = None,
        version: str = "1.0.0",
    ) -> None:
        """Register a tool and automatically add it to the catalog."""
        self._tools[name] = {
            "handler": handler,
            "description": description,
            "parameters": parameters or {},
        }
        # L3.4: mirror into catalog
        self.catalog.register(
            ToolCatalogEntry(
                name=name,
                layer=layer,
                category=category,
                tags=tags or [],
                version=version,
                description=description,
            )
        )

    def get(self, name: str) -> dict[str, Any] | None:
        """Get tool info by name."""
        return self._tools.get(name)

    def list_tools(self) -> list[dict[str, Any]]:
        """List all registered tools."""
        return [
            {"name": n, "description": t["description"], "parameters": t["parameters"]}
            for n, t in self._tools.items()
        ]

    def get_catalog(self) -> ToolCatalog:
        """Return the L3.4 tool catalog."""
        return self.catalog
