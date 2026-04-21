"""Tests for KGMemoryTool."""

import asyncio
from unittest.mock import MagicMock

import pytest

from src.memory.knowledge_graph import KnowledgeGraph
from src.memory.kg_query_interface import KGQueryInterface
from src.memory.tools.kg_memory_tool import KGMemoryTool


@pytest.fixture
def kg(tmp_path):
    """Create a temporary KnowledgeGraph."""
    return KnowledgeGraph(db_path=str(tmp_path / "test_kg.db"))


@pytest.fixture
def kg_qi(kg):
    return KGQueryInterface(kg)


@pytest.fixture
def tool(kg_qi):
    return KGMemoryTool(kg_qi)


class TestKGMemoryToolDefinition:
    def test_tool_definition_structure(self):
        defn = KGMemoryTool.get_tool_definition()
        assert defn["name"] == "kg_memory_query"
        assert "operation" in defn["parameters"]["properties"]
        assert "params" in defn["parameters"]["properties"]

    def test_tool_definition_operations(self):
        defn = KGMemoryTool.get_tool_definition()
        ops = defn["parameters"]["properties"]["operation"]["enum"]
        assert "search_entities" in ops
        assert "expand" in ops
        assert "get_neighbors" in ops
        assert "shortest_path" in ops
        assert "stats" in ops


class TestKGMemoryToolExecute:
    def test_stats(self, tool, kg):
        result = tool.execute("stats", {})
        assert isinstance(result, dict)
        assert "entity_count" in result
        assert "relation_count" in result

    def test_search_entities_empty(self, tool):
        result = tool.execute("search_entities", {"query": "test"})
        assert isinstance(result, list)
        assert len(result) == 0

    def test_unknown_operation_raises(self, tool):
        with pytest.raises(ValueError, match="Unknown operation"):
            tool.execute("invalid_op", {})

    def test_execute_async(self, kg_qi):
        tool = KGMemoryTool(kg_qi)
        result = asyncio.run(tool.execute_async("stats", {}))
        assert isinstance(result, dict)


class TestKGQueryInterfaceDirect:
    @pytest.mark.asyncio
    async def test_query_search_entities(self, kg_qi):
        result = await kg_qi.query("agent-1", "search_entities", {"query": "foo"})
        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_query_stats(self, kg_qi):
        result = await kg_qi.query("agent-1", "stats", {})
        assert "entity_count" in result

    @pytest.mark.asyncio
    async def test_query_unknown(self, kg_qi):
        with pytest.raises(ValueError):
            await kg_qi.query("agent-1", "no_such_op", {})

    @pytest.mark.asyncio
    async def test_query_get_neighbors_no_entity(self, kg_qi):
        result = await kg_qi.query("agent-1", "get_neighbors", {"entity_name": "nonexistent"})
        assert result == []
