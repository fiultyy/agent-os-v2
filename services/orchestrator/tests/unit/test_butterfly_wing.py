# tests/unit/test_butterfly_wing.py
"""Unit tests for butterfly_wing module — D-15 Butterfly Model.

Tests all 18 features:
  F1-F6: Forward wing (metadata/score/threshold/trigger/output/cache)
  B1-B6: Backward wing (metadata/score/threshold/trigger/output/cache)
  C1-C6: Coordination (lifecycle/persistence/debug/API/perf constraints)
"""

import json
import os
import sys
import tempfile
import uuid
from datetime import datetime, timezone, timedelta

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from memory.butterfly_wing import (
    WingType,
    WingMetadata,
    ButterflyWing,
    ButterflyEngine,
    ButterflyStore,
    ButterflyRecallStrategy,
    get_default_store,
)


# ─────────────────────────────────────────────────────────────────
# F1 / B1: WingMetadata dataclass
# ─────────────────────────────────────────────────────────────────

class TestWingMetadata:
    def test_forward_metadata_default_values(self):
        """F1: WingMetadata for forward wing has correct defaults."""
        meta = WingMetadata(wing=WingType.FORWARD)
        assert meta.wing == WingType.FORWARD
        assert meta.strength == 1.0
        assert meta.confidence == 0.5
        assert meta.expires_at is None
        assert meta.trigger_type == ""
        assert meta.association_desc == ""

    def test_backward_metadata_default_values(self):
        """B1: WingMetadata for backward wing has correct defaults."""
        meta = WingMetadata(wing=WingType.BACKWARD)
        assert meta.wing == WingType.BACKWARD
        assert meta.strength == 1.0
        assert meta.confidence == 0.5

    def test_wing_metadata_expiry_check(self):
        """F1/B1: is_expired() correctly checks expiry."""
        past = datetime.now(timezone.utc) - timedelta(hours=1)
        meta_past = WingMetadata(wing=WingType.FORWARD, expires_at=past)
        assert meta_past.is_expired() is True

        future = datetime.now(timezone.utc) + timedelta(hours=1)
        meta_future = WingMetadata(wing=WingType.FORWARD, expires_at=future)
        assert meta_future.is_expired() is False

        meta_no_expiry = WingMetadata(wing=WingType.FORWARD)
        assert meta_no_expiry.is_expired() is False


# ─────────────────────────────────────────────────────────────────
# F2-F5 / B2-B5: ButterflyWing dataclass
# ─────────────────────────────────────────────────────────────────

class TestButterflyWing:
    def test_create_empty(self):
        """F2/B2: ButterflyWing.create_empty() initializes correctly."""
        wing = ButterflyWing.create_empty()
        assert wing.forward_metadata is not None
        assert wing.backward_metadata is not None
        assert wing.forward_score == 0.0
        assert wing.backward_score == 0.0
        assert wing.composite_score == 0.0
        assert wing.is_active is False
        assert wing.forward_associations == []
        assert wing.backward_associations == []

    def test_to_dict_from_dict_roundtrip(self):
        """F5/B5: ButterflyWing serializes and deserializes correctly."""
        wing = ButterflyWing(
            forward_metadata=WingMetadata(
                wing=WingType.FORWARD,
                strength=0.8,
                confidence=0.7,
                trigger_type="temporal",
                association_desc="test forward",
                expires_at=datetime(2026, 4, 25, 12, 0, 0, tzinfo=timezone.utc),
            ),
            forward_score=0.75,
            forward_associations=["time_1", "entity_a"],
            backward_metadata=WingMetadata(
                wing=WingType.BACKWARD,
                strength=0.6,
                confidence=0.5,
                trigger_type="citation",
                association_desc="test backward",
            ),
            backward_score=0.5,
            backward_associations=["usage_x"],
            composite_score=0.625,
            is_active=True,
        )

        d = wing.to_dict()
        assert d["forward_score"] == 0.75
        assert d["forward_associations"] == ["time_1", "entity_a"]
        assert d["backward_score"] == 0.5
        assert d["backward_associations"] == ["usage_x"]
        assert d["composite_score"] == 0.625
        assert d["is_active"] is True

        restored = ButterflyWing.from_dict(d)
        assert restored.forward_score == 0.75
        assert restored.backward_score == 0.5
        assert restored.forward_associations == ["time_1", "entity_a"]
        assert restored.composite_score == 0.625
        assert restored.is_active is True
        assert restored.forward_metadata.trigger_type == "temporal"


# ─────────────────────────────────────────────────────────────────
# F2-F6 / B2-B6: ButterflyEngine computation
# ─────────────────────────────────────────────────────────────────

class TestButterflyEngineForwardWing:
    """F2-F6: Forward wing computation."""

    def test_forward_wing_with_temporal_context(self):
        """F4/F5: Forward wing triggers on temporal context."""
        engine = ButterflyEngine()
        meta, score, assoc = engine.compute_forward_wing(
            content="Meeting about project X",
            temporal_context=["monday", "april"],
            entity_context=[],
            thematic_context=[],
        )

        assert meta.wing == WingType.FORWARD
        assert meta.trigger_type == "temporal"
        assert score > 0
        assert "monday" in assoc
        assert "april" in assoc

    def test_forward_wing_with_entity_context(self):
        """F4/F5: Forward wing triggers on entity context."""
        engine = ButterflyEngine()
        meta, score, assoc = engine.compute_forward_wing(
            content="Claude is working on Agent OS",
            temporal_context=[],
            entity_context=["Claude", "Agent OS"],
            thematic_context=[],
        )

        assert meta.trigger_type == "entity"
        assert "Claude" in assoc
        assert "Agent OS" in assoc

    def test_forward_wing_with_thematic_context(self):
        """F4/F5: Forward wing falls back to thematic context."""
        engine = ButterflyEngine()
        meta, score, assoc = engine.compute_forward_wing(
            content="Some content",
            temporal_context=[],
            entity_context=[],
            thematic_context=["machine learning", "RAG"],
        )

        assert meta.trigger_type == "thematic"
        assert "machine learning" in assoc

    def test_forward_wing_score_range(self):
        """F2/F3: Forward wing score is bounded 0-1."""
        engine = ButterflyEngine()

        _, low_score, _ = engine.compute_forward_wing(
            content="test",
            temporal_context=[],
            entity_context=[],
            thematic_context=[],
            recency=0.1,
            activation_freq=0.1,
        )

        _, high_score, _ = engine.compute_forward_wing(
            content="test",
            temporal_context=["t1"],
            entity_context=["e1"],
            thematic_context=["th1"],
            recency=1.0,
            activation_freq=1.0,
        )

        assert 0.0 <= low_score <= 1.0
        assert 0.0 <= high_score <= 1.0

    def test_forward_wing_empty_context(self):
        """F2/F5: Forward wing handles empty context gracefully."""
        engine = ButterflyEngine()
        meta, score, assoc = engine.compute_forward_wing(
            content="test",
            temporal_context=[],
            entity_context=[],
            thematic_context=[],
        )

        assert meta.wing == WingType.FORWARD
        assert score >= 0.0
        assert isinstance(assoc, list)


class TestButterflyEngineBackwardWing:
    """B2-B6: Backward wing computation."""

    def test_backward_wing_with_citations(self):
        """B4/B5: Backward wing triggers on citations."""
        engine = ButterflyEngine()
        meta, score, assoc = engine.compute_backward_wing(
            content="Important research finding",
            citations=5,
            usage_context=["used in paper A", "cited by team B"],
        )

        assert meta.wing == WingType.BACKWARD
        assert meta.trigger_type == "citation"
        assert score > 0
        assert "used in paper A" in assoc

    def test_backward_wing_with_usage_context(self):
        """B4/B5: Backward wing falls back to usage context."""
        engine = ButterflyEngine()
        meta, score, assoc = engine.compute_backward_wing(
            content="Some memory",
            citations=0,
            usage_context=["daily standup", "sprint planning"],
        )

        assert meta.trigger_type == "usage"
        assert "daily standup" in assoc

    def test_backward_wing_score_capped_at_1(self):
        """B2/B3: Backward wing score is capped at 1.0."""
        engine = ButterflyEngine()
        _, score, _ = engine.compute_backward_wing(
            content="test",
            citations=100,  # Would be 10.0 without cap
            usage_context=[],
        )

        assert score <= 1.0

    def test_backward_wing_no_citations(self):
        """B2: Backward wing with zero citations has low score."""
        engine = ButterflyEngine()
        _, score, _ = engine.compute_backward_wing(
            content="test",
            citations=0,
            usage_context=[],
        )

        assert score < 0.1  # Near zero without citations


class TestButterflyEngineComposite:
    """C1-C2: Composite score coordination."""

    def test_composite_score_equal_balance(self):
        """C1: Equal weights give simple average."""
        engine = ButterflyEngine()
        score = engine.compute_composite_score(0.6, 0.4, balance=(1.0, 1.0))
        assert score == 0.5

    def test_composite_score_weighted_forward(self):
        """C1: Forward-biased balance weights forward more."""
        engine = ButterflyEngine()
        score = engine.compute_composite_score(0.8, 0.2, balance=(3.0, 1.0))
        # (0.8*3 + 0.2*1) / 4 = 2.6/4 = 0.65
        assert score == pytest.approx(0.65)

    def test_composite_score_weighted_backward(self):
        """C1: Backward-biased balance weights backward more."""
        engine = ButterflyEngine()
        score = engine.compute_composite_score(0.2, 0.8, balance=(1.0, 3.0))
        # (0.2*1 + 0.8*3) / 4 = 2.6/4 = 0.65
        assert score == pytest.approx(0.65)

    def test_composite_score_zero_weights(self):
        """C1: Zero total balance returns 0."""
        engine = ButterflyEngine()
        score = engine.compute_composite_score(0.5, 0.5, balance=(0.0, 0.0))
        assert score == 0.0

    def test_is_wing_active_above_threshold(self):
        """C2: Wing is active when composite > threshold."""
        engine = ButterflyEngine()
        assert engine.is_wing_active(0.75, threshold=0.7) is True
        assert engine.is_wing_active(0.7, threshold=0.7) is False  # exactly = not active
        assert engine.is_wing_active(0.5, threshold=0.7) is False

    def test_is_wing_active_default_threshold(self):
        """C2: Default threshold is 0.7."""
        engine = ButterflyEngine()
        assert engine.is_wing_active(0.8) is True
        assert engine.is_wing_active(0.6) is False


class TestButterflyEngineScoreMemory:
    """Full scoring pipeline."""

    def test_score_memory_full_pipeline(self):
        """All wings computed together in score_memory()."""
        engine = ButterflyEngine()
        wing = engine.score_memory(
            content="Agent OS architecture discussion",
            temporal_context=["monday", "sprint 3"],
            entity_context=["orchestrator", "memory system"],
            thematic_context=["agent frameworks"],
            citations=3,
            usage_context=["used in design doc"],
        )

        assert wing.forward_score > 0
        assert wing.backward_score > 0
        assert wing.composite_score > 0
        assert wing.forward_associations
        assert wing.backward_associations
        assert wing.forward_metadata is not None
        assert wing.backward_metadata is not None

        # TTL set on metadata (3 days)
        assert wing.forward_metadata.expires_at is not None
        assert wing.backward_metadata.expires_at is not None


# ─────────────────────────────────────────────────────────────────
# C4 / B3: ButterflyStore persistence
# ─────────────────────────────────────────────────────────────────

class TestButterflyStore:
    """ButterflyStore persistence and lifecycle."""

    def setup_method(self):
        self.db_fd, self.db_path = tempfile.mkstemp(suffix=".db")
        self.store = ButterflyStore(db_path=self.db_path)

    def teardown_method(self):
        self.store.close()
        os.close(self.db_fd)
        os.unlink(self.db_path)

    def test_save_and_load_wing(self):
        """B3/C4: Save and load a butterfly wing."""
        memory_id = str(uuid.uuid4())
        wing = ButterflyWing(
            forward_metadata=WingMetadata(
                wing=WingType.FORWARD,
                strength=0.8,
                trigger_type="temporal",
            ),
            forward_score=0.75,
            forward_associations=["t1", "e1"],
            backward_metadata=WingMetadata(
                wing=WingType.BACKWARD,
                strength=0.6,
                trigger_type="citation",
            ),
            backward_score=0.5,
            backward_associations=["u1"],
            composite_score=0.625,
            is_active=False,
        )

        self.store.save_wing(memory_id, wing)
        loaded = self.store.load_wing(memory_id)

        assert loaded is not None
        assert loaded.forward_score == 0.75
        assert loaded.forward_associations == ["t1", "e1"]
        assert loaded.backward_score == 0.5
        assert loaded.composite_score == 0.625

    def test_load_nonexistent_returns_none(self):
        """B3: Loading non-existent wing returns None."""
        result = self.store.load_wing("nonexistent-id")
        assert result is None

    def test_delete_wing(self):
        """B3: Delete removes wing from store."""
        memory_id = str(uuid.uuid4())
        wing = ButterflyWing.create_empty()
        self.store.save_wing(memory_id, wing)

        deleted = self.store.delete_wing(memory_id)
        assert deleted is True

        loaded = self.store.load_wing(memory_id)
        assert loaded is None

    def test_delete_nonexistent_returns_false(self):
        """B3: Deleting non-existent wing returns False."""
        result = self.store.delete_wing("nonexistent")
        assert result is False

    def test_compute_ttl(self):
        """C4: TTL is 3 days (259200 seconds)."""
        assert self.store.compute_ttl() == 3 * 24 * 3600

    def test_cleanup_expired(self):
        """C4: cleanup_expired removes expired entries."""
        # Insert an expired wing manually via raw SQL
        past = datetime.now(timezone.utc) - timedelta(hours=1)
        memory_id = str(uuid.uuid4())
        wing = ButterflyWing.create_empty()
        wing.forward_score = 0.5

        with self.store._conn:
            self.store._conn.execute(
                """INSERT INTO butterfly_wings
                   (memory_id, wing_data, created_at, expires_at,
                    forward_score, backward_score, composite_score, is_active)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    memory_id,
                    json.dumps(wing.to_dict()),
                    past.isoformat(),
                    past.isoformat(),  # Already expired
                    0.5, 0.0, 0.25, 0,
                ),
            )

        # Insert a non-expired wing
        future = datetime.now(timezone.utc) + timedelta(hours=1)
        memory_id2 = str(uuid.uuid4())
        with self.store._conn:
            self.store._conn.execute(
                """INSERT INTO butterfly_wings
                   (memory_id, wing_data, created_at, expires_at,
                    forward_score, backward_score, composite_score, is_active)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    memory_id2,
                    json.dumps(wing.to_dict()),
                    past.isoformat(),
                    future.isoformat(),  # Not expired
                    0.5, 0.0, 0.25, 0,
                ),
            )

        removed = self.store.cleanup_expired()
        assert removed == 1

        # Expired one should be gone
        assert self.store.load_wing(memory_id) is None
        # Non-expired one should remain
        assert self.store.load_wing(memory_id2) is not None

    def test_get_wing_details(self):
        """C5: get_wing_details returns structured dict."""
        memory_id = str(uuid.uuid4())
        wing = ButterflyWing(
            forward_metadata=WingMetadata(
                wing=WingType.FORWARD,
                strength=0.8,
                confidence=0.7,
                trigger_type="temporal",
            ),
            forward_score=0.75,
            forward_associations=["t1", "e1"],
            backward_metadata=WingMetadata(
                wing=WingType.BACKWARD,
                strength=0.6,
                confidence=0.5,
                trigger_type="citation",
            ),
            backward_score=0.5,
            backward_associations=["u1"],
            composite_score=0.625,
            is_active=True,
        )
        self.store.save_wing(memory_id, wing)

        details = self.store.get_wing_details(memory_id)
        assert "forward" in details
        assert "backward" in details
        assert len(details["forward"]) == 1
        assert details["forward"][0]["score"] == 0.75
        assert details["forward"][0]["associations"] == ["t1", "e1"]
        assert details["backward"][0]["score"] == 0.5

    def test_get_wing_details_nonexistent(self):
        """C5: get_wing_details for missing ID returns empty wings."""
        details = self.store.get_wing_details("nonexistent")
        assert details == {"forward": [], "backward": []}


# ─────────────────────────────────────────────────────────────────
# C5: ButterflyRecallStrategy
# ─────────────────────────────────────────────────────────────────

class MockRecallStrategy:
    """Minimal recall strategy for testing ButterflyRecallStrategy."""

    def __init__(self, items: list):
        self._items = items

    async def recall(self, query, agent_id="", session_id="", memory_type=None, scope=None, top_k=10):
        return list(self._items[:top_k])


class TestButterflyRecallStrategy:
    """ButterflyRecallStrategy wing filtering."""

    def setup_method(self):
        self.db_fd, self.db_path = tempfile.mkstemp(suffix=".db")
        self.store = ButterflyStore(db_path=self.db_path)

    def teardown_method(self):
        self.store.close()
        os.close(self.db_fd)
        os.unlink(self.db_path)

    def _make_item(self, id_: str, content: str = "test content"):
        """Create a minimal dict-like memory item for testing."""
        return {"id": id_, "content": content}

    def test_recall_returns_base_results_when_no_wing(self):
        """C5: Items without wings are included when wing is None."""
        # Patch ButterflyStore.load_wing to return None
        items = [self._make_item("mem1"), self._make_item("mem2")]
        mock = MockRecallStrategy(items)
        strategy = ButterflyRecallStrategy(mock, self.store)

        results = self._run_sync(strategy.recall, "test", top_k=5, wing=None)
        # All items included when no wings exist
        assert len(results) == 2

    def test_recall_filters_by_forward_wing(self):
        """C5/B5: wing='forward' filters to forward-high-scoring items."""
        # Create two memories with different wings
        item1 = self._make_item("mem1")
        item2 = self._make_item("mem2")

        # Save wings for both
        wing1 = ButterflyWing(
            forward_metadata=WingMetadata(wing=WingType.FORWARD, strength=0.9),
            forward_score=0.9,
            forward_associations=["a1"],
            backward_score=0.1,
            composite_score=0.75,  # Above threshold so wing check runs
            is_active=False,
        )
        wing2 = ButterflyWing(
            forward_metadata=WingMetadata(wing=WingType.FORWARD, strength=0.2),
            forward_score=0.2,  # Low forward score
            backward_score=0.8,
            backward_associations=["b1"],
            composite_score=0.75,  # Above threshold so wing check runs
            is_active=False,
        )

        self.store.save_wing("mem1", wing1)
        self.store.save_wing("mem2", wing2)

        mock = MockRecallStrategy([item1, item2])
        strategy = ButterflyRecallStrategy(mock, self.store)

        results = self._run_sync(strategy.recall, "test", top_k=5, wing="forward")
        assert len(results) == 1
        assert results[0]["id"] == "mem1"

    def test_recall_filters_by_backward_wing(self):
        """C5/B5: wing='backward' filters to backward-high-scoring items."""
        item1 = self._make_item("mem1")
        item2 = self._make_item("mem2")

        wing1 = ButterflyWing(
            forward_score=0.1,
            backward_metadata=WingMetadata(wing=WingType.BACKWARD, strength=0.9),
            backward_score=0.9,
            backward_associations=["u1"],
            composite_score=0.75,  # Above threshold so wing check runs
            is_active=False,
        )
        wing2 = ButterflyWing(
            forward_score=0.8,
            backward_metadata=WingMetadata(wing=WingType.BACKWARD, strength=0.1),
            backward_score=0.1,  # Low backward score
            composite_score=0.75,  # Above threshold so wing check runs
            is_active=False,
        )

        self.store.save_wing("mem1", wing1)
        self.store.save_wing("mem2", wing2)

        mock = MockRecallStrategy([item1, item2])
        strategy = ButterflyRecallStrategy(mock, self.store)

        results = self._run_sync(strategy.recall, "test", top_k=5, wing="backward")
        assert len(results) == 1
        assert results[0]["id"] == "mem1"

    def test_recall_threshold_filters_low_composite(self):
        """C5: Items below composite threshold are filtered out."""
        item1 = self._make_item("mem1")
        item2 = self._make_item("mem2")

        wing_high = ButterflyWing(
            forward_score=0.8,
            backward_score=0.8,
            composite_score=0.8,
            is_active=True,
        )
        wing_low = ButterflyWing(
            forward_score=0.3,
            backward_score=0.3,
            composite_score=0.3,  # Below default 0.7 threshold
            is_active=False,
        )

        self.store.save_wing("mem1", wing_high)
        self.store.save_wing("mem2", wing_low)

        mock = MockRecallStrategy([item1, item2])
        strategy = ButterflyRecallStrategy(mock, self.store)

        results = self._run_sync(strategy.recall, "test", top_k=5, threshold=0.7)
        assert len(results) == 1
        assert results[0]["id"] == "mem1"

    def _run_sync(self, coro, *args, **kwargs):
        """Run an async coroutine synchronously for testing."""
        import asyncio
        return asyncio.get_event_loop().run_until_complete(coro(*args, **kwargs))


# ─────────────────────────────────────────────────────────────────
# C5: Module-level get_default_store
# ─────────────────────────────────────────────────────────────────

class TestDefaultStore:
    def test_get_default_store_creates_instance(self):
        """C5: get_default_store() creates a global store."""
        # Use a temp path to avoid interference
        import shutil
        temp_path = tempfile.mktemp(suffix=".db")
        store1 = get_default_store(db_path=temp_path)
        store2 = get_default_store(db_path=temp_path)
        # Should be the same instance (singleton)
        assert store1 is store2
        store1.close()
        if os.path.exists(temp_path):
            os.unlink(temp_path)
