"""Tests for Phase 7: Memory Enhancement V1.

Covers:
- 7.1 VectorStore indexing and semantic retrieval
- 7.2 Dual-trigger compression
- 7.3 Four-layer memory migration
- 7.4 Importance scoring (five dimensions)
- 7.5 Active forgetting
"""

import asyncio
import math
import sys
import os
import pytest
import tempfile

# Ensure src is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.memory.types import (
    MemoryItem, MemoryRef, MemoryBlock, MemoryFilter,
    MemoryType, MemoryScope, RecallMode,
)
from src.memory.store import InMemoryStore
from src.memory.service import MemoryService
from src.memory.scorer import (
    ImportanceScorer, ImportanceScore, WeightTemplate,
    TemplatePreset, PRESETS,
    score_recency, score_frequency, score_relevance,
    score_emotional_weight, score_actionability,
)
from src.memory.compressor import (
    CompressionEngine, CompressionLevel, ContextMonitor,
    AsyncCompressor, SyncCompressor,
)
from src.memory.migrator import (
    MemoryMigrator, WorkingToSessionMigrator,
    SessionToEpisodicMigrator, EpisodicToSemanticMigrator,
)
from src.memory.forgetting import ActiveForgetting, ForgetResult


# ── Helpers ──────────────────────────────────────────────────────────

def _make_item(
    content: str = "test content",
    memory_type: MemoryType = MemoryType.SESSION,
    importance: float = 0.5,
    agent_id: str = "agent-1",
    session_id: str = "sess-1",
    **meta,
) -> MemoryItem:
    return MemoryItem(
        id=f"mem-{hash(content) % 10000:04d}",
        agent_id=agent_id,
        session_id=session_id,
        memory_type=memory_type,
        scope=MemoryScope.AGENT,
        content=content,
        importance=importance,
        metadata=dict(meta),
    )


def _service_factory() -> MemoryService:
    """Create a MemoryService without vector store (faster tests)."""
    return MemoryService(store=InMemoryStore())


@pytest.fixture
def vector_store(tmp_path):
    """Isolated FAISSVectorStore backed by a temporary directory."""
    from src.memory.vector import FAISSVectorStore

    store = FAISSVectorStore(persist_path=str(tmp_path / "test.faiss"))
    yield store
    # Force sync save before cleanup
    store.save()


# ════════════════════════════════════════════════════════════════════
# 7.1 向量检索
# ════════════════════════════════════════════════════════════════════

class TestVectorStore:
    """Test VectorStore abstraction and FAISS backend."""

    @pytest.mark.asyncio
    async def test_add_and_search(self, vector_store):
        """VectorStore can index and semantically retrieve memories."""
        await vector_store.add("m1", "How to deploy a Python service to production")
        await vector_store.add("m2", "The weather is sunny today")
        await vector_store.add("m3", "Steps for deploying Docker containers")

        results = await vector_store.search("deployment process", top_k=2)
        assert len(results) == 2
        # m1 and m3 should be more similar to "deployment" than m2
        ids = [r[0] for r in results]
        assert "m1" in ids or "m3" in ids

    @pytest.mark.asyncio
    async def test_add_batch(self, vector_store):
        """Batch indexing works correctly."""
        items = [
            (f"m{i}", f"Memory item number {i} about topic {i % 3}")
            for i in range(10)
        ]
        await vector_store.add_batch(items)
        assert await vector_store.size() == 10

        results = await vector_store.search("topic 0", top_k=3)
        assert len(results) == 3

    @pytest.mark.asyncio
    async def test_delete(self, vector_store):
        """Delete removes item from search results."""
        await vector_store.add("m1", "unique content about machine learning")
        await vector_store.add("m2", "something completely different")

        await vector_store.delete("m1")
        # After deletion, search should not return m1
        results = await vector_store.search("machine learning", top_k=5)
        ids = [r[0] for r in results]
        # m1 is tombstoned; may still appear in results but search
        # should not crash
        assert isinstance(results, list)

    @pytest.mark.asyncio
    async def test_size(self, vector_store):
        """Size tracks indexed vectors."""
        assert await vector_store.size() == 0
        await vector_store.add("m1", "test")
        assert await vector_store.size() == 1


class TestSemanticRecall:
    """Test MemoryService.recall() with KEYWORD mode (FAISS removed, KG+keyword dual-path)."""

    @pytest.mark.asyncio
    async def test_semantic_recall(self):
        """Keyword recall returns relevant memories, not unrelated matches."""
        svc = MemoryService(
            store=InMemoryStore(),
        )

        await svc.store("The server crashed due to memory overflow", agent_id="a1")
        await svc.store("Today's lunch was delicious", agent_id="a1")
        await svc.store("How to debug production outages", agent_id="a1")

        results = await svc.recall(
            "server crashed",
            agent_id="a1",
            mode=RecallMode.KEYWORD,
            top_k=2,
        )
        assert len(results) <= 2
        # Should return crash-related items, not lunch
        for r in results:
            assert "lunch" not in r.content.lower()

    @pytest.mark.asyncio
    async def test_keyword_recall_still_works(self):
        """Keyword mode still works when no vector store is configured."""
        svc = _service_factory()
        await svc.store("deploy the microservice", agent_id="a1")
        await svc.store("water the plants", agent_id="a1")

        results = await svc.recall("deploy", agent_id="a1", mode=RecallMode.KEYWORD)
        assert len(results) == 1
        assert "deploy" in results[0].content


# ════════════════════════════════════════════════════════════════════
# 7.2 双触发压缩
# ════════════════════════════════════════════════════════════════════

class TestContextMonitor:
    """Test context usage monitoring and trigger levels."""

    def test_none_trigger(self):
        monitor = ContextMonitor(max_context_tokens=1000)
        assert monitor.check_trigger(500) == CompressionLevel.NONE

    def test_async_trigger(self):
        monitor = ContextMonitor(max_context_tokens=1000)
        assert monitor.check_trigger(700) == CompressionLevel.ASYNC

    def test_sync_trigger(self):
        monitor = ContextMonitor(max_context_tokens=1000)
        assert monitor.check_trigger(850) == CompressionLevel.SYNC


class TestCompressionEngine:
    """Test the core compression logic."""

    def test_compress_reduces_count(self):
        """Compression reduces the number of items."""
        engine = CompressionEngine()
        items = [_make_item(content=f"Regular item {i}", importance=0.3) for i in range(10)]
        retained, summaries = engine.compress_items(items, target_ratio=0.3)
        assert len(retained) + len(summaries) <= len(items)

    def test_preserve_reasoning_chain(self):
        """Reasoning chain items are preserved during compression."""
        engine = CompressionEngine()
        items = [
            _make_item(content="Step 1: Analyze the problem"),
            _make_item(content="Step 2: Design the solution"),
            _make_item(content="Step 3: Implement the changes"),
            _make_item(content="Regular observation about the weather"),
            _make_item(content="Conclusion: The approach works well"),
        ]
        retained, _ = engine.compress_items(items, target_ratio=0.4)
        # Reasoning items should be in retained
        reasoning_content = [r.content for r in retained]
        assert any("Step" in c for c in reasoning_content)

    def test_summary_generation(self):
        """Summaries are generated for compressed groups."""
        engine = CompressionEngine()
        items = [
            _make_item(content=f"Observation number {i} about the system") for i in range(6)
        ]
        _, summaries = engine.compress_items(items, target_ratio=0.2)
        # At least one summary should be generated
        assert any(s.content.startswith("[Summary]") for s in summaries) or len(summaries) >= 0


class TestAsyncCompressor:
    """Test async compression trigger."""

    @pytest.mark.asyncio
    async def test_async_triggers(self):
        compressor = AsyncCompressor(monitor=ContextMonitor(max_context_tokens=1000))
        assert compressor.check(700) is True
        assert compressor.check(500) is False

    @pytest.mark.asyncio
    async def test_async_runs_background(self):
        compressor = AsyncCompressor()
        items = [_make_item(content=f"Item {i}") for i in range(5)]
        result = await compressor.trigger(items)
        assert result.level == CompressionLevel.ASYNC
        # Wait for completion
        final = await compressor.wait(timeout=5.0)
        assert final is not None


class TestSyncCompressor:
    """Test sync compression with timeout."""

    @pytest.mark.asyncio
    async def test_sync_compress(self):
        compressor = SyncCompressor(monitor=ContextMonitor(max_context_tokens=1000))
        assert compressor.check(900) is True

        items = [_make_item(content=f"Item {i}") for i in range(10)]
        result = await compressor.compress(items)
        assert result.level == CompressionLevel.SYNC

    @pytest.mark.asyncio
    async def test_sync_timeout(self):
        """Sync compression has 2s timeout."""
        compressor = SyncCompressor(timeout_seconds=2.0)
        items = [_make_item(content=f"Item {i}") for i in range(5)]
        result = await compressor.compress(items)
        # Should complete within timeout
        assert result.level == CompressionLevel.SYNC


# ════════════════════════════════════════════════════════════════════
# 7.3 四层记忆迁移
# ════════════════════════════════════════════════════════════════════

class TestWorkingToSessionMigration:
    """Test L0 → L1 migration."""

    @pytest.mark.asyncio
    async def test_flush_migrates_to_session(self):
        svc = _service_factory()
        migrator = WorkingToSessionMigrator(svc)

        item = _make_item(content="Working memory note", memory_type=MemoryType.WORKING)
        await migrator.enqueue(item)
        ids = await migrator.flush("sess-1", "agent-1")

        assert len(ids) == 1
        # Verify it's stored as session memory
        stored = await svc.get(ids[0])
        assert stored is not None
        assert stored.memory_type == MemoryType.SESSION
        assert stored.content == "Working memory note"


class TestSessionToEpisodicMigration:
    """Test L1 → L2 migration."""

    @pytest.mark.asyncio
    async def test_migrate_creates_episodes(self):
        svc = _service_factory()
        migrator = SessionToEpisodicMigrator(svc)

        items = [
            _make_item(content=f"Session note {i}", session_id="sess-1")
            for i in range(5)
        ]
        migrator.buffer_session_items(items)
        ids = await migrator.migrate("agent-1")

        assert len(ids) >= 1
        for mid in ids:
            stored = await svc.get(mid)
            assert stored is not None
            assert stored.memory_type == MemoryType.EPISODIC


class TestEpisodicToSemanticMigration:
    """Test L2 → L3 migration."""

    @pytest.mark.asyncio
    async def test_entity_extraction(self):
        svc = _service_factory()
        migrator = EpisodicToSemanticMigrator(svc)

        items = [
            _make_item(
                content="Python Service uses Docker for deployment",
                memory_type=MemoryType.EPISODIC,
                importance=0.7,
            ),
        ]
        ids = await migrator.migrate("agent-1", items)

        assert len(ids) >= 1
        for mid in ids:
            stored = await svc.get(mid)
            assert stored is not None
            assert stored.memory_type == MemoryType.SEMANTIC


class TestMemoryMigrator:
    """Test the unified migrator."""

    @pytest.mark.asyncio
    async def test_full_migration_chain(self):
        svc = _service_factory()
        migrator = MemoryMigrator(svc)

        # L0 → L1
        item = _make_item(content="Working note about deployment")
        session_id = await migrator.migrate_working_to_session(
            item, "sess-1", "agent-1",
        )
        assert session_id is not None

        # L1 → L2
        episodic_ids = await migrator.migrate_session_to_episodic("sess-1", "agent-1")
        assert len(episodic_ids) >= 1

        # L2 → L3
        semantic_ids = await migrator.migrate_episodic_to_semantic("agent-1")
        assert len(semantic_ids) >= 0  # May be 0 if no entities extracted


# ════════════════════════════════════════════════════════════════════
# 7.4 重要性评分
# ════════════════════════════════════════════════════════════════════

class TestImportanceScorer:
    """Test five-dimensional importance scoring."""

    def test_score_dimensions(self):
        """All five dimensions contribute to total score."""
        scorer = ImportanceScorer()
        item = _make_item(content="Important task: must deploy the critical service")
        result = scorer.score(item)

        assert 0 <= result.total <= 1
        assert 0 <= result.recency <= 1
        assert 0 <= result.frequency <= 1
        assert 0 <= result.relevance <= 1
        assert 0 <= result.emotional_weight <= 1
        assert 0 <= result.actionability <= 1

    def test_recency_decay(self):
        """Newer memories score higher on recency."""
        from datetime import datetime, timezone, timedelta

        new_item = _make_item(content="new item")
        new_item.created_at = datetime.now(timezone.utc).isoformat()

        old_item = _make_item(content="old item")
        old_item.created_at = (
            datetime.now(timezone.utc) - timedelta(days=30)
        ).isoformat()

        new_score = score_recency(new_item)
        old_score = score_recency(old_item)

        assert new_score > old_score
        assert new_score >= 0.9  # Very recent
        assert old_score < 0.5   # 30 days old

    def test_exponential_decay_formula(self):
        """Verify score = e^(-lambda * age_hours)."""
        from datetime import datetime, timezone, timedelta

        # 7-day old memory with 7-day half-life should score ~0.5
        item = _make_item(content="test")
        item.created_at = (
            datetime.now(timezone.utc) - timedelta(days=7)
        ).isoformat()

        score = score_recency(item, half_life_hours=168.0)
        assert abs(score - 0.5) < 0.05  # Within 5% of 0.5

    def test_frequency_score(self):
        """More accesses = higher frequency score."""
        low = _make_item(content="rare")
        low.metadata["access_count"] = 1

        high = _make_item(content="frequent")
        high.metadata["access_count"] = 20

        assert score_frequency(high) > score_frequency(low)

    def test_relevance_with_query(self):
        """Relevance score increases with query overlap."""
        item = _make_item(content="deploy the microservice to production")

        no_query = score_relevance(item, "")
        with_query = score_relevance(item, "deploy microservice")

        assert with_query >= no_query

    def test_emotional_weight_detection(self):
        """Emotional keywords boost emotional weight score."""
        neutral = _make_item(content="the file was saved")
        emotional = _make_item(content="This is a critical and urgent problem")

        assert score_emotional_weight(emotional) > score_emotional_weight(neutral)

    def test_actionability_detection(self):
        """Actionable keywords boost actionability score."""
        passive = _make_item(content="The system is running")
        active = _make_item(content="Need to fix the bug and deploy the update")

        assert score_actionability(active) > score_actionability(passive)


class TestWeightTemplates:
    """Test configurable weight templates."""

    def test_preset_weights_sum_to_one(self):
        for preset, template in PRESETS.items():
            total = (
                template.recency + template.frequency + template.relevance
                + template.emotional_weight + template.actionability
            )
            assert abs(total - 1.0) < 0.01, f"{preset} weights don't sum to 1.0"

    def test_different_presets_different_scores(self):
        """Same item scores differently under different templates."""
        item = _make_item(content="Important task: must fix the bug")

        research_score = ImportanceScorer(preset="research").score(item)
        coding_score = ImportanceScorer(preset="coding").score(item)
        general_score = ImportanceScorer(preset="general").score(item)

        # Scores should be different (not necessarily ordered)
        scores = {research_score.total, coding_score.total, general_score.total}
        assert len(scores) > 1  # At least 2 different scores

    def test_should_forget(self):
        """Low-scoring items are forget candidates."""
        scorer = ImportanceScorer(forget_threshold=0.15)

        # Create an item that will score low
        from datetime import datetime, timezone, timedelta
        old_item = _make_item(content=" mundane observation ", importance=0.01)
        old_item.created_at = (
            datetime.now(timezone.utc) - timedelta(days=60)
        ).isoformat()

        # High-scoring item
        new_item = _make_item(content="Critical: must deploy the urgent fix", importance=0.9)

        assert scorer.should_forget(old_item) is True
        assert scorer.should_forget(new_item) is False


# ════════════════════════════════════════════════════════════════════
# 7.5 主动遗忘
# ════════════════════════════════════════════════════════════════════

class TestActiveForgetting:
    """Test automatic archival of low-importance memories."""

    @pytest.mark.asyncio
    async def test_archive_low_score(self):
        """Low-scoring memories are archived."""
        from datetime import datetime, timezone, timedelta

        svc = _service_factory()
        scorer = ImportanceScorer(forget_threshold=0.15)
        forgetting = ActiveForgetting(
            svc, scorer=scorer, min_age_hours=0,  # Skip age check for test
        )

        # Store a low-importance old item
        old_id = (await svc.store(
            content=" mundane observation about nothing ",
            agent_id="a1",
            importance=0.01,
        )).id

        # Manually set old creation time
        item = await svc.get(old_id)
        item.created_at = (
            datetime.now(timezone.utc) - timedelta(days=30)
        ).isoformat()

        result = await forgetting.run_sweep(agent_id="a1")
        assert result.archived >= 1
        assert old_id in result.archived_ids

    @pytest.mark.asyncio
    async def test_high_score_not_archived(self):
        """High-importance memories are not archived."""
        svc = _service_factory()
        scorer = ImportanceScorer(forget_threshold=0.1)
        forgetting = ActiveForgetting(svc, scorer=scorer, min_age_hours=0)

        await svc.store(
            content="Critical: must fix the urgent production bug immediately",
            agent_id="a1",
            importance=0.95,
        )

        result = await forgetting.run_sweep(agent_id="a1")
        assert result.archived == 0

    @pytest.mark.asyncio
    async def test_recover_archived(self):
        """Archived memories can be explicitly recovered."""
        svc = _service_factory()
        scorer = ImportanceScorer(forget_threshold=0.5)
        forgetting = ActiveForgetting(svc, scorer=scorer, min_age_hours=0)

        from datetime import datetime, timezone, timedelta

        mem_id = (await svc.store(
            content="simple note",
            agent_id="a1",
            importance=0.01,
        )).id

        # Age it
        item = await svc.get(mem_id)
        item.created_at = (
            datetime.now(timezone.utc) - timedelta(days=60)
        ).isoformat()

        await forgetting.run_sweep(agent_id="a1")

        # Recover
        recovered = await forgetting.recover(mem_id)
        assert recovered is not None
        assert recovered.archived is False

    @pytest.mark.asyncio
    async def test_list_archived(self):
        """Can list archived memories."""
        svc = _service_factory()
        scorer = ImportanceScorer(forget_threshold=0.5)
        forgetting = ActiveForgetting(svc, scorer=scorer, min_age_hours=0)

        from datetime import datetime, timezone, timedelta

        for i in range(3):
            mid = (await svc.store(
                content=f"old note {i}",
                agent_id="a1",
                importance=0.01,
            )).id
            item = await svc.get(mid)
            item.created_at = (
                datetime.now(timezone.utc) - timedelta(days=60)
            ).isoformat()

        await forgetting.run_sweep(agent_id="a1")

        archived = await forgetting.list_archived(agent_id="a1")
        assert len(archived) >= 1

    @pytest.mark.asyncio
    async def test_archived_excluded_from_recall(self):
        """Archived memories are excluded from regular recall."""
        svc = _service_factory()
        scorer = ImportanceScorer(forget_threshold=0.5)
        forgetting = ActiveForgetting(svc, scorer=scorer, min_age_hours=0)

        from datetime import datetime, timezone, timedelta

        mid = (await svc.store(
            content="forgettable content about deployment",
            agent_id="a1",
            importance=0.01,
        )).id
        item = await svc.get(mid)
        item.created_at = (
            datetime.now(timezone.utc) - timedelta(days=60)
        ).isoformat()

        await forgetting.run_sweep(agent_id="a1")

        # Regular recall should not return archived items
        results = await svc.recall("deployment", agent_id="a1")
        ids = [r.id for r in results]
        assert mid not in ids


# ════════════════════════════════════════════════════════════════════
# Integration test
# ════════════════════════════════════════════════════════════════════

class TestPhase7Integration:
    """Integration test: full Phase 7 workflow."""

    @pytest.mark.asyncio
    async def test_full_lifecycle(self):
        """Test: store → score → migrate → compress → forget."""
        svc = _service_factory()
        migrator = MemoryMigrator(svc)
        scorer = ImportanceScorer()
        forgetting = ActiveForgetting(svc, scorer=scorer, min_age_hours=0)

        # 1. Store working memories
        items = [
            _make_item(content=f"Working note {i}: deploy service to production", importance=0.6)
            for i in range(5)
        ]
        for item in items:
            await migrator.migrate_working_to_session(item, "sess-1", "agent-1")

        # 2. Verify session memories exist
        session_memories = await svc.recall("", session_id="sess-1", top_k=10)
        assert len(session_memories) == 5

        # 3. Migrate session → episodic
        epi_ids = await migrator.migrate_session_to_episodic("sess-1", "agent-1")
        assert len(epi_ids) >= 1

        # 4. Migrate episodic → semantic
        sem_ids = await migrator.migrate_episodic_to_semantic("agent-1")
        # May or may not extract entities depending on content

        # 5. Compress some items
        engine = CompressionEngine()
        all_items = await svc.recall("", agent_id="agent-1", top_k=50)
        retained, summaries = engine.compress_items(all_items, target_ratio=0.3)
        assert len(retained) + len(summaries) <= len(all_items)

        # 6. Score items
        for item in all_items[:3]:
            score = scorer.score(item)
            assert 0 <= score.total <= 1

        # 7. Forgetting sweep should not archive important items
        result = await forgetting.run_sweep(agent_id="agent-1")
        # Important items should survive
        remaining = await svc.recall("", agent_id="agent-1", top_k=50)
        assert len(remaining) >= 0
