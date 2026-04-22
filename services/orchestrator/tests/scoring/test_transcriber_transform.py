"""Tests for Transcriber TaskSpec transformation."""
import pytest


def test_transcriber_output_schema():
    """Transcriber output matches TRANSCRIBER_SPEC.output_schema."""
    from src.memory.sideline.transcriber import SidelineTranscriber
    from src.memory.knowledge_graph import KnowledgeGraph

    kg = KnowledgeGraph()
    t = SidelineTranscriber(knowledge_graph=kg)

    action_units = [
        {
            "id": "au1",
            "timestamp": "2026-01-01T00:00:00Z",
            "user_intent": "debug memory overflow",
            "tool_calls": [{"name": "search", "params": {}}],
            "tool_results": [{"name": "search", "result": "found issue"}],
        },
        {
            "id": "au2",
            "timestamp": "2026-01-01T00:01:00Z",
            "user_intent": "hi there",
            "tool_calls": [],
            "tool_results": [],
        },
    ]

    result = t.process_action_units(action_units)

    # Schema check
    assert "relevant_facts" in result
    assert "discarded_facts" in result
    assert "extraction_metadata" in result
    assert "total_action_units" in result["extraction_metadata"]

    # Content check
    assert result["extraction_metadata"]["total_action_units"] == 2
    assert result["extraction_metadata"]["relevant_count"] == 1
    assert result["extraction_metadata"]["discarded_count"] == 1
    assert len(result["relevant_facts"]) == 1
    assert len(result["discarded_facts"]) == 1


def test_classification_has_tool_calls():
    """Action units with tool calls are classified as relevant."""
    from src.memory.sideline.transcriber import SidelineTranscriber
    from src.memory.knowledge_graph import KnowledgeGraph

    kg = KnowledgeGraph()
    t = SidelineTranscriber(knowledge_graph=kg)

    au = {
        "id": "au1",
        "timestamp": "2026-01-01T00:00:00Z",
        "user_intent": "deploy service",
        "tool_calls": [{"name": "kubectl", "params": {"cmd": "apply"}}],
        "tool_results": [{"name": "kubectl", "result": "ok"}],
    }

    result = t.process_action_units([au])
    assert len(result["relevant_facts"]) == 1
    assert result["relevant_facts"][0]["outcome"] in ("success", "failure")


def test_classification_no_tool_calls():
    """Action units without tool calls are classified as discarded."""
    from src.memory.sideline.transcriber import SidelineTranscriber
    from src.memory.knowledge_graph import KnowledgeGraph

    kg = KnowledgeGraph()
    t = SidelineTranscriber(knowledge_graph=kg)

    au = {
        "id": "au1",
        "timestamp": "2026-01-01T00:00:00Z",
        "user_intent": "what is the weather",
        "tool_calls": [],
        "tool_results": [],
    }

    result = t.process_action_units([au])
    assert len(result["discarded_facts"]) == 1


def test_classification_trivial_intent():
    """Trivial intents are classified as discarded even with tool calls."""
    from src.memory.sideline.transcriber import SidelineTranscriber
    from src.memory.knowledge_graph import KnowledgeGraph

    kg = KnowledgeGraph()
    t = SidelineTranscriber(knowledge_graph=kg)

    au = {
        "id": "au1",
        "timestamp": "2026-01-01T00:00:00Z",
        "user_intent": "hi",
        "tool_calls": [{"name": "search", "params": {}}],
        "tool_results": [],
    }

    result = t.process_action_units([au])
    assert len(result["discarded_facts"]) == 1
    assert result["discarded_facts"][0]["id"] == "au1"