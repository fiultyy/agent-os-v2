"""KG Memory Tool — 给 Agent 用的 KG 查询工具。

封装 :class:`KGQueryInterface` 为 Tool，供 Agent 通过 tool registry 直接调用。
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from src.memory.kg_query_interface import KGQueryInterface


class KGMemoryTool:
    """封装 KGQueryInterface 为 Tool，供 Agent 直接调用。

    Usage::

        kg_qi = KGQueryInterface(knowledge_graph)
        tool = KGMemoryTool(kg_qi)
        result = tool.execute("search_entities", {"query": "auth"})
    """

    def __init__(
        self,
        kg_query_interface: KGQueryInterface,
        max_workers: int = 4,
    ) -> None:
        self._qi = kg_query_interface
        self._executor = ThreadPoolExecutor(max_workers=max_workers)

    def close(self) -> None:
        """Shutdown the thread pool. Call on cleanup."""
        self._executor.shutdown(wait=True)

    def execute(self, operation: str, params: dict) -> dict | list | None:
        """Execute KG query synchronously (for tool registry)."""
        return self._qi.query("system", operation, params)

    async def execute_async(self, operation: str, params: dict) -> dict | list | None:
        """Execute KG query asynchronously.

        Since KGQueryInterface.query() is synchronous, this wraps it
        in asyncio.to_thread() to avoid blocking the event loop.
        """
        return await asyncio.to_thread(
            self._qi.query, "system", operation, params,
        )

    @staticmethod
    def get_tool_definition() -> dict:
        """返回 tool 注册定义。"""
        return {
            "name": "kg_memory_query",
            "description": (
                "Query historical context via Knowledge Graph. "
                "Use to find past tasks, understand causal chains, "
                "or search for entities by name/type."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "operation": {
                        "type": "string",
                        "enum": [
                            "search_entities",
                            "expand",
                            "get_neighbors",
                            "shortest_path",
                            "stats",
                        ],
                    },
                    "params": {"type": "object"},
                },
                "required": ["operation", "params"],
            },
        }
