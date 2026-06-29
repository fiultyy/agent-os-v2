"""Tests for UnifiedRecall — P4 keyword + KG fusion."""


import pytest

from src.memory._recall.unified_recall import UnifiedRecall
from src.memory.types import MemoryItem


# ── Helpers ──────────────────────────────────────────────────────────

def _make_item(item_id: str, content: str, importance: float = 0.5) -> MemoryItem:
    return MemoryItem(
        id=item_id,
        content=content,
        importance=importance,
        agent_id="agent-1",
        session_id="sess-1",
    )


class FakeStrategy:
    """Minimal recall strategy for testing."""

    def __init__(self, results: list[MemoryItem] | None = None, *, fail: bool = False):
        self._results = results or []
        self._fail = fail

    async def recall(self, query, agent_id, session_id, memory_type, scope, top_k):
        if self._fail:
            raise RuntimeError("strategy exploded")
        return self._results[:top_k]


# ── Tests ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dual_path_merge():
    """Both paths return results; items are merged and scored."""
    kw = FakeStrategy([
        _make_item("m1", "hello world", importance=0.8),
        _make_item("m2", "foo bar", importance=0.6),
    ])
    kg = FakeStrategy([
        _make_item("m1", "hello world", importance=0.9),  # overlap
        _make_item("m3", "baz qux", importance=0.7),
    ])

    unified = UnifiedRecall(keyword_recall=kw, kg_recall=kg)
    results = await unified.recall(
        "hello", "agent-1", "sess-1", None, None, top_k=10,
    )

    ids = [r.id for r in results]
    assert "m1" in ids
    assert "m2" in ids
    assert "m3" in ids
    assert len(results) == 3

    # m1 appears in both paths → combined score should be highest
    m1 = next(r for r in results if r.id == "m1")
    assert m1.metadata["_kw_score"] == 0.8
    assert m1.metadata["_kg_score"] == 0.9
    expected_combined = 0.8 * 0.4 + 0.9 * 0.6  # 0.32 + 0.54 = 0.86
    assert abs(m1.metadata["_combined_score"] - expected_combined) < 1e-6


@pytest.mark.asyncio
async def test_one_path_fails_gracefully():
    """If one path raises, the other still returns results."""
    kw = FakeStrategy([
        _make_item("m1", "hello", importance=0.5),
    ])
    kg = FakeStrategy(fail=True)

    unified = UnifiedRecall(keyword_recall=kw, kg_recall=kg)
    results = await unified.recall(
        "hello", "agent-1", "sess-1", None, None, top_k=10,
    )

    assert len(results) == 1
    assert results[0].id == "m1"


@pytest.mark.asyncio
async def test_deduplication():
    """Same id from both paths → single item with merged scores."""
    kw = FakeStrategy([_make_item("m1", "hello", importance=0.5)])
    kg = FakeStrategy([_make_item("m1", "hello", importance=0.5)])

    unified = UnifiedRecall(keyword_recall=kw, kg_recall=kg)
    results = await unified.recall(
        "hello", "agent-1", "sess-1", None, None, top_k=10,
    )

    assert len(results) == 1
    assert results[0].id == "m1"
    # Should have both scores
    assert "_kw_score" in results[0].metadata
    assert "_kg_score" in results[0].metadata


@pytest.mark.asyncio
async def test_score_weighting():
    """Verify combined score = kw_weight * kw_score + kg_weight * kg_score."""
    kw = FakeStrategy([_make_item("m1", "a", importance=1.0)])
    kg = FakeStrategy([_make_item("m2", "b", importance=1.0)])

    unified = UnifiedRecall(
        keyword_recall=kw, kg_recall=kg,
        keyword_weight=0.3, kg_weight=0.7,
    )
    results = await unified.recall(
        "x", "agent-1", "sess-1", None, None, top_k=10,
    )

    by_id = {r.id: r for r in results}
    assert abs(by_id["m1"].metadata["_combined_score"] - 1.0 * 0.3) < 1e-6
    assert abs(by_id["m2"].metadata["_combined_score"] - 1.0 * 0.7) < 1e-6


@pytest.mark.asyncio
async def test_top_k_truncation():
    """Results are truncated to top_k."""
    items_kw = [_make_item(f"kw-{i}", f"item {i}", importance=0.5) for i in range(20)]
    items_kg = [_make_item(f"kg-{i}", f"item {i}", importance=0.5) for i in range(20)]

    kw = FakeStrategy(items_kw)
    kg = FakeStrategy(items_kg)

    unified = UnifiedRecall(keyword_recall=kw, kg_recall=kg)
    results = await unified.recall(
        "item", "agent-1", "sess-1", None, None, top_k=5,
    )

    assert len(results) == 5


@pytest.mark.asyncio
async def test_empty_results():
    """Both paths return empty → result is empty."""
    kw = FakeStrategy([])
    kg = FakeStrategy([])

    unified = UnifiedRecall(keyword_recall=kw, kg_recall=kg)
    results = await unified.recall(
        "nothing", "agent-1", "sess-1", None, None, top_k=10,
    )

    assert results == []


@pytest.mark.asyncio
async def test_kg_only_result_has_kg_match():
    """Items found only by KG have _kg_match metadata."""
    kw = FakeStrategy([])
    kg = FakeStrategy([_make_item("m1", "hello", importance=0.8)])

    unified = UnifiedRecall(keyword_recall=kw, kg_recall=kg)
    results = await unified.recall(
        "hello", "agent-1", "sess-1", None, None, top_k=10,
    )

    assert len(results) == 1
    assert results[0].metadata.get("_kg_match") is True


@pytest.mark.asyncio
async def test_sorting_by_combined_score():
    """Results are sorted by _combined_score descending."""
    kw = FakeStrategy([
        _make_item("low", "low kw", importance=0.2),
        _make_item("high", "high kw", importance=0.9),
    ])
    kg = FakeStrategy([
        _make_item("mid", "mid kg", importance=0.5),
    ])

    unified = UnifiedRecall(keyword_recall=kw, kg_recall=kg)
    results = await unified.recall(
        "x", "agent-1", "sess-1", None, None, top_k=10,
    )

    scores = [r.metadata["_combined_score"] for r in results]
    assert scores == sorted(scores, reverse=True)
