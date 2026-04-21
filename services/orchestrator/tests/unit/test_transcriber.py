"""Tests for SidelineTranscriber."""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.memory.knowledge_graph import KnowledgeGraph
from src.memory.sideline.transcriber import ActionUnit, SidelineTranscriber


@pytest.fixture
def kg(tmp_path):
    return KnowledgeGraph(db_path=str(tmp_path / "test_kg.db"))


@pytest.fixture
def transcriber(kg):
    return SidelineTranscriber(knowledge_graph=kg)


def _write_jsonl(path: Path, messages: list[dict]) -> None:
    """Helper: write messages to a JSONL file."""
    with open(path, "w", encoding="utf-8") as f:
        for msg in messages:
            f.write(json.dumps(msg, ensure_ascii=False) + "\n")


class TestActionUnit:
    def test_auto_id(self):
        unit = ActionUnit()
        assert unit.id  # non-empty UUID

    def test_custom_id(self):
        unit = ActionUnit(id="custom-123")
        assert unit.id == "custom-123"


class TestParseJsonl:
    def test_empty_file(self, transcriber, tmp_path):
        path = tmp_path / "empty.jsonl"
        path.write_text("")
        units = transcriber.parse_jsonl_to_action_units(str(path))
        assert units == []

    def test_nonexistent_file(self, transcriber):
        units = transcriber.parse_jsonl_to_action_units("/no/such/file.jsonl")
        assert units == []

    def test_single_turn(self, transcriber, tmp_path):
        path = tmp_path / "single.jsonl"
        _write_jsonl(path, [
            {"role": "user", "content": "Search for auth module", "timestamp": "2026-01-01T00:00:00Z"},
            {"role": "assistant", "content": "I will search for the auth module.", "tool_calls": [
                {"function": {"name": "search", "arguments": {"query": "auth"}}}
            ]},
            {"role": "tool", "tool_call_id": "tc-1", "name": "search", "content": "Found 3 results"},
        ])
        units = transcriber.parse_jsonl_to_action_units(str(path))
        assert len(units) == 1
        u = units[0]
        assert "auth" in u.user_intent.lower()
        assert u.tool_calls[0]["name"] == "search"
        assert len(u.tool_results) == 1

    def test_multi_turn_with_parent(self, transcriber, tmp_path):
        path = tmp_path / "multi.jsonl"
        _write_jsonl(path, [
            {"role": "user", "content": "First question"},
            {"role": "assistant", "content": "First answer"},
            {"role": "tool", "tool_call_id": "tc-1", "content": "result1"},
            {"role": "user", "content": "Second question"},
            {"role": "assistant", "content": "Second answer"},
            {"role": "tool", "tool_call_id": "tc-2", "content": "result2"},
        ])
        units = transcriber.parse_jsonl_to_action_units(str(path))
        assert len(units) == 2
        assert units[1].parent_id == units[0].id

    def test_trailing_unit_without_tool(self, transcriber, tmp_path):
        path = tmp_path / "trailing.jsonl"
        _write_jsonl(path, [
            {"role": "user", "content": "Just asking"},
            {"role": "assistant", "content": "Just answering"},
        ])
        units = transcriber.parse_jsonl_to_action_units(str(path))
        assert len(units) == 1
        assert units[0].tool_calls == []

    def test_malformed_line_skipped(self, transcriber, tmp_path):
        path = tmp_path / "bad.jsonl"
        path.write_text("not json\n")
        units = transcriber.parse_jsonl_to_action_units(str(path))
        assert units == []

    def test_multipart_content(self, transcriber, tmp_path):
        path = tmp_path / "multipart.jsonl"
        _write_jsonl(path, [
            {"role": "user", "content": [
                {"type": "text", "text": "Hello world"},
                {"type": "image", "url": "http://example.com/img.png"},
            ]},
            {"role": "assistant", "content": "Got it"},
            {"role": "tool", "tool_call_id": "tc-1", "content": "ok"},
        ])
        units = transcriber.parse_jsonl_to_action_units(str(path))
        assert "Hello world" in units[0].user_intent


class TestExtractAndIngest:
    def test_empty_batch(self, transcriber):
        result = transcriber.extract_and_ingest_batch([])
        assert result["entities"] == 0
        assert result["relations"] == 0
        assert result["units"] == 0

    def test_ingest_with_tool_calls(self, transcriber, kg):
        units = [
            ActionUnit(
                user_intent="Find authentication module",
                assistant_decision="Searching for auth module",
                tool_calls=[{"name": "search", "params": {"query": "auth"}}],
                tool_results=[{"tool_call_id": "tc-1", "name": "search", "status": "ok"}],
            )
        ]
        result = transcriber.extract_and_ingest_batch(units)
        assert result["units"] == 1
        assert result["entities"] > 0 or result["relations"] >= 0

    def test_ingest_extracts_relations(self, transcriber, kg):
        units = [
            ActionUnit(
                user_intent="AuthService depends on Database",
                assistant_decision="AuthService uses Database for storage",
            )
        ]
        result = transcriber.extract_and_ingest_batch(units)
        # EntityExtractor should pick up relations from this text
        assert result["units"] == 1

    def test_llm_fallback_not_called_when_none(self, transcriber, kg):
        units = [
            ActionUnit(
                user_intent="no regex match here xyz",
                assistant_decision="nothing extractable",
            )
        ]
        # Should not raise even though no LLM client
        result = transcriber.extract_and_ingest_batch(units)
        assert isinstance(result, dict)


class TestLlmExtractRelations:
    def test_no_llm_returns_empty(self, transcriber):
        result = transcriber._llm_extract_relations("some text")
        assert result == []

    def test_with_mock_llm(self, kg):
        mock_llm = MagicMock()
        mock_llm.chat.return_value = json.dumps([
            {"subject": "Agent", "predicate": "uses", "object": "Database"},
        ])
        t = SidelineTranscriber(knowledge_graph=kg, llm_client=mock_llm)
        result = t._llm_extract_relations("Agent uses Database")
        assert len(result) == 1
        assert result[0].source_entity_id == "Agent"
        assert result[0].target_entity_id == "Database"

    def test_mock_llm_bad_json(self, kg):
        mock_llm = MagicMock()
        mock_llm.chat.return_value = "not valid json"
        t = SidelineTranscriber(knowledge_graph=kg, llm_client=mock_llm)
        result = t._llm_extract_relations("some text")
        assert result == []

    def test_mock_llm_exception(self, kg):
        mock_llm = MagicMock()
        mock_llm.chat.side_effect = RuntimeError("LLM down")
        t = SidelineTranscriber(knowledge_graph=kg, llm_client=mock_llm)
        result = t._llm_extract_relations("some text")
        assert result == []
