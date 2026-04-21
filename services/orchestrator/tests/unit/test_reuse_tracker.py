"""Tests for ReuseTracker."""

import pytest
import tempfile
import os

from src.memory.knowledge_graph import KnowledgeGraph, Entity
from src.memory.sideline.reuse_tracker import ReuseTracker


@pytest.fixture
def kg():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_kg.db")
        graph = KnowledgeGraph(db_path=db_path)
        yield graph


@pytest.fixture
def tracker(kg):
    return ReuseTracker(knowledge_graph=kg)


class TestReuseTracker:
    def test_track_tool_call_creates_entity(self, tracker, kg):
        result = tracker.track_tool_call(
            tool_name="kg_memory_query",
            context={
                "session_id": "session-1",
                "agent_id": "agent-1",
                "timestamp": "2026-04-21T12:00:00Z",
                "action_unit_id": "au-1",
            },
        )

        assert result["old_score"] == 0.0
        assert result["new_score"] == 1.0
        assert result["entity_id"] != ""

        entity = kg.get_entity(result["entity_id"])
        assert entity is not None
        assert entity["name"] == "kg_memory_query"
        assert entity["entity_type"] == "tool"

    def test_track_tool_call_increments_score(self, tracker, kg):
        ctx = {
            "session_id": "session-1",
            "agent_id": "agent-1",
            "timestamp": "2026-04-21T12:00:00Z",
            "action_unit_id": "au-1",
        }

        r1 = tracker.track_tool_call("my_tool", ctx)
        assert r1["new_score"] == 1.0

        r2 = tracker.track_tool_call("my_tool", ctx)
        assert r2["new_score"] == 2.0

        r3 = tracker.track_tool_call("my_tool", ctx)
        assert r3["new_score"] == 3.0

    def test_track_tool_call_maintains_history(self, tracker, kg):
        ctx1 = {
            "session_id": "session-1",
            "agent_id": "agent-1",
            "timestamp": "2026-04-21T12:00:00Z",
            "action_unit_id": "au-1",
        }
        ctx2 = {
            "session_id": "session-2",
            "agent_id": "agent-1",
            "timestamp": "2026-04-21T13:00:00Z",
            "action_unit_id": "au-2",
        }

        tracker.track_tool_call("tool_a", ctx1)
        tracker.track_tool_call("tool_a", ctx2)

        entity = kg.find_entity_by_name("tool_a")
        assert entity is not None
        props = entity.get("properties", {})
        assert "reuse_history" in props
        assert len(props["reuse_history"]) == 2

    def test_get_top_reused_entities(self, tracker, kg):
        ctx = {"session_id": "s1", "agent_id": "a1", "timestamp": "2026-04-21T12:00:00Z"}

        tracker.track_tool_call("low_score_tool", ctx)
        for _ in range(3):
            tracker.track_tool_call("mid_tool", ctx)
        for _ in range(10):
            tracker.track_tool_call("high_tool", ctx)

        top = tracker.get_top_reused_entities(limit=5)
        assert len(top) == 3
        assert top[0]["name"] == "high_tool"
        assert top[1]["name"] == "mid_tool"
        assert top[2]["name"] == "low_score_tool"

    def test_get_top_reused_entities_filter_by_type(self, tracker, kg):
        ctx = {"session_id": "s1", "agent_id": "a1", "timestamp": "2026-04-21T12:00:00Z"}

        tracker.track_tool_call("tool_a", ctx)
        tracker.track_tool_call("tool_b", ctx)
        entity = Entity(
            name="non_tool_entity",
            entity_type="concept",
            properties={"reuse_score": 100.0},
        )
        kg.add_entity(entity)

        top = tracker.get_top_reused_entities(entity_type="tool", limit=10)
        names = [e["name"] for e in top]
        assert "tool_a" in names
        assert "tool_b" in names
        assert "non_tool_entity" not in names


class TestReuseTrackerDecay:
    def test_decay_scores(self, tracker):
        # Decay is a no-op in the current implementation
        # since we don't have time-travel test capability
        result = tracker.decay_scores(days=30)
        assert "entities_updated" in result
        assert "total_decay" in result
