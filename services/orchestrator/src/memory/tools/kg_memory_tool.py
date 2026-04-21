"""KG Memory Tool — 给 Agent 用的 KG 查询工具。

封装 :class:`KGQueryInterface` 为 Tool，供 Agent 通过 tool registry 直接调用。
"""

from __future__ import annotations

import asyncio
from typing import Any

from src.memory.kg_query_interface import KGQueryInterface


class KGMemoryTool:
    """封装 KGQueryInterface 为 Tool，供 Agent 直接调用。

    Usage::

        kg_qi = KGQueryInterface(knowledge_graph)
        tool = KGMemoryTool(kg_qi)
        result = tool.execute("search_entities", {"query": "auth"})
    """

    def __init__(self, kg_query_interface: KGQueryInterface) -> None:
        self._qi = kg_query_interface

    def execute(self, operation: str, params: dict) -> dict | list | None:
        """同步执行 KG 查询（供 tool registry 使用）。

        内部将 async 调用包装为同步接口。

        Args:
            operation: 查询操作名。
            params: 操作参数。

        Returns:
            查询结果。
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            # 已在 async 上下文中 — 用 create_task 避免嵌套 run
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(
                    asyncio.run,
                    self._qi.query("system", operation, params),
                )
                return future.result(timeout=30)
        else:
            return asyncio.run(
                self._qi.query("system", operation, params),
            )

    async def execute_async(self, operation: str, params: dict) -> dict | list | None:
        """异步执行 KG 查询。

        Args:
            operation: 查询操作名。
            params: 操作参数。

        Returns:
            查询结果。
        """
        return await self._qi.query("system", operation, params)

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
