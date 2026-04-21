"""KGQueryInterface — Agent 直接查询 KG 的受控接口。

实现 Meta-Harness 的"文件系统即接口"理念：
Agent 通过受控的图查询方法直接查询 KG，无 SQL 拼接风险。
"""

from __future__ import annotations

from typing import Any

from src.memory.knowledge_graph import KnowledgeGraph


class KGQueryInterface:
    """受控 KG 查询接口，限制可查询范围防止注入。

    所有查询通过预定义的 operation 路由执行，
    不接受原始 SQL 或任意字符串拼接。
    """

    def __init__(self, knowledge_graph: KnowledgeGraph) -> None:
        self._kg = knowledge_graph

    def query(self, agent_id: str, operation: str, params: dict) -> dict | list | None:
        """执行受控 KG 查询。

        Args:
            agent_id: 调用方 Agent ID（用于审计/权限）。
            operation: 查询操作名。
            params: 操作参数。

        Returns:
            查询结果。

        Raises:
            ValueError: 未知操作名。
        """
        dispatch = {
            "search_entities": self._op_search_entities,
            "expand": self._op_expand,
            "get_neighbors": self._op_get_neighbors,
            "shortest_path": self._op_shortest_path,
            "stats": self._op_stats,
        }

        handler = dispatch.get(operation)
        if handler is None:
            raise ValueError(f"Unknown operation: {operation}")

        return handler(params)

    # ── Operation handlers (private, no SQL拼接) ────────────────

    def _op_search_entities(self, params: dict) -> list[dict[str, Any]]:
        return self._kg.search_entities(
            query=params["query"],
            entity_type=params.get("entity_type"),
            limit=params.get("limit", 20),
        )

    def _op_expand(self, params: dict) -> dict[str, Any]:
        return self._kg.expand(
            entity_name=params["entity_name"],
            depth=params.get("depth", 2),
            relation_type=params.get("relation_type"),
        )

    def _op_get_neighbors(self, params: dict) -> list[dict[str, Any]]:
        entity_id = self._kg._resolve_entity_id(params["entity_name"])
        if not entity_id:
            return []
        return self._kg.query_neighbors(
            entity_id=entity_id,
            rel_type=params.get("rel_type"),
            max_depth=params.get("max_depth", 1),
        )

    def _op_shortest_path(self, params: dict) -> list[dict[str, Any]] | None:
        return self._kg.shortest_path(
            source_name=params["source_name"],
            target_name=params["target_name"],
        )

    def _op_stats(self, params: dict) -> dict[str, Any]:
        return self._kg.stats()

    # ── Tool definition ────────────────────────────────────────────

    def get_tool_definition(self) -> dict:
        """返回 LLM Tool 定义，供 Agent 注册使用。"""
        return {
            "name": "kg_memory_query",
            "description": (
                "Query the Knowledge Graph to explore entities, relations, "
                "and paths. Use when you need to find historical context, "
                "understand causal chains, or search for specific "
                "tasks/tools/decisions from past sessions."
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
                        "description": "The query operation to perform",
                    },
                    "params": {
                        "type": "object",
                        "description": "Operation parameters (see documentation)",
                    },
                },
                "required": ["operation", "params"],
            },
        }
