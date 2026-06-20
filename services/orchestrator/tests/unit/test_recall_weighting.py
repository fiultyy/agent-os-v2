# tests/unit/test_recall_weighting.py
"""Unit tests for Part 2 ⑦ weighted recall — ``match × lif`` item scoring.

Design refs:
  - docs/memory-kernel-design.md §5 (recall is a computation engine)
  - docs/memory-kernel-impl-plan.md Part 2 ⑦

Verifies:
  - ``match_item`` (keyword substring + verbatim bonus, clamped [0,1])
  - ``lif_item`` (rank-based percentile, no absolute suppression — high
    review correction)
  - ``rank_items`` (``score = match × lif``, sorted desc; score computed
    per **memory item**, not "relation group g")
  - field absent ⇒ pure match order (Part 1 equivalent, no suppression)
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

try:
    import pysqlite3  # type: ignore[import-untyped]
    sys.modules["sqlite3"] = pysqlite3
    sys.modules["sqlite3.dbapi2"] = pysqlite3.dbapi2
except ImportError:
    pass

from memory._recall.weighted_recall import (  # noqa: E402
    rank_items,
    match_item,
    lif_item,
    item_concepts,
    query_tokens,
)
from memory.types import MemoryItem  # noqa: E402


# ── Fixtures ────────────────────────────────────────────────────────


def _item(content: str, mid: str = "") -> MemoryItem:
    return MemoryItem(id=mid or content[:8], content=content, agent_id="a")


# ── match_item ──────────────────────────────────────────────────────


class TestMatchItem:
    def test_zero_on_no_overlap(self):
        assert match_item(_item("hello world"), ["python"], "python") == 0.0

    def test_partial_token_hit(self):
        # 1 of 2 tokens ⇒ 0.5
        score = match_item(
            _item("deploy with python"), ["deploy", "rust"], "deploy rust",
        )
        assert score == pytest.approx(0.5)

    def test_full_token_hit_plus_verbatim_bonus_clamped(self):
        score = match_item(
            _item("deploy rust service"), ["deploy", "rust"], "deploy rust",
        )
        # all hit (1.0) + verbatim (+0.2) ⇒ clamped 1.0
        assert score == 1.0

    def test_case_insensitive(self):
        assert match_item(_item("PYTHON is great"), ["python"], "python") > 0.0

    def test_no_tokens_returns_zero(self):
        assert match_item(_item("x"), [], "") == 0.0


# ── lif_item (rank-based, no absolute suppression) ─────────────────


class TestLifItem:
    def test_empty_field_returns_one(self):
        """field None/empty ⇒ 1.0 (no suppression, Part 1 equivalent)."""
        assert lif_item(_item("anything"), None) == 1.0
        assert lif_item(_item("anything"), {}) == 1.0

    def test_item_touching_top_concept_gets_high_weight(self):
        field = {"rust": 0.9, "python": 0.05}
        # item content "rust" is the top concept → rank percentile ~1.0
        w = lif_item(_item("rust language"), field)
        assert w == pytest.approx(1.0)

    def test_item_touching_bottom_concept_gets_low_weight(self):
        field = {"rust": 0.9, "python": 0.05}
        # "python" is the lowest concept → percentile ~0.0
        w = lif_item(_item("python language"), field)
        assert w == pytest.approx(0.0)

    def test_item_touching_no_field_concept(self):
        """Item content matches no concept in field ⇒ lif 0.0 (not 1.0).

        The concepts don't overlap → item_concepts empty → mean of empty
        set ⇒ 0.0 (the NeuralFieldEngine.lif_weight contract)."""
        field = {"rust": 0.9, "python": 0.05}
        w = lif_item(_item("golang language"), field)
        assert w == 0.0

    def test_custom_lif_weight_fn_injected(self):
        """Caller can inject a custom lif_weight pure function."""
        field = {"x": 0.5}

        def custom(concepts, fld):
            return 0.42

        w = lif_item(_item("x content"), field, lif_weight_fn=custom)
        assert w == pytest.approx(0.42)

    def test_no_absolute_suppression_when_field_low(self):
        """high review correction: rank-based, not absolute.

        Even if absolute potentials are tiny (0.02), the top concept
        still gets weight ~1.0 (rank-based), so it isn't buried."""
        field = {"rust": 0.02, "python": 0.01}
        w = lif_item(_item("rust"), field)
        assert w == pytest.approx(1.0)  # top concept ⇒ ~1.0, not 0.02


# ── rank_items (match × lif, sorted desc, per item) ────────────────


class TestRankItems:
    def test_empty_returns_empty(self):
        assert rank_items([], "query", None) == []

    def test_score_is_match_times_lif(self):
        items = [_item("rust deploy", "m1")]
        ranked = rank_items(items, "rust deploy", field={"rust": 0.9})
        assert len(ranked) == 1
        r = ranked[0]
        # match = 1.0 (full + verbatim), lif = 1.0 (top concept)
        assert r["match"] == pytest.approx(1.0)
        assert r["lif"] == pytest.approx(1.0)
        assert r["score"] == pytest.approx(1.0)

    def test_ranking_orders_by_match_times_lif(self):
        """score = match × lif determines order, per item (not group g)."""
        items = [
            _item("rust language primer", "m_rust"),    # touches "rust" high pot
            _item("python language primer", "m_py"),    # touches "python" low pot
        ]
        ranked = rank_items(
            items, "language", field={"rust": 0.9, "python": 0.05},
        )
        # both match "language" equally ⇒ lif breaks the tie
        assert ranked[0]["item"].id == "m_rust"
        assert ranked[0]["score"] > ranked[1]["score"]

    def test_no_field_pure_match_order(self):
        """field None ⇒ lif=1.0 ⇒ pure match order (Part 1 equivalent)."""
        items = [
            _item("deploy notes", "m_partial"),     # 1/2 tokens
            _item("deploy rust notes", "m_full"),   # 2/2 tokens + verbatim
        ]
        ranked = rank_items(items, "deploy rust", field=None)
        assert ranked[0]["item"].id == "m_full"
        assert ranked[0]["lif"] == pytest.approx(1.0)

    def test_top_k_truncation(self):
        items = [_item(f"python item {i}", f"m{i}") for i in range(5)]
        ranked = rank_items(items, "python", field=None, top_k=2)
        assert len(ranked) == 2

    def test_monotonic_non_increasing_scores(self):
        items = [
            _item("rust language primer", "m_rust"),
            _item("python language primer", "m_py"),
            _item("golang notes", "m_go"),  # no match
        ]
        ranked = rank_items(
            items, "language", field={"rust": 0.9, "python": 0.05},
        )
        scores = [r["score"] for r in ranked]
        assert scores == sorted(scores, reverse=True)

    def test_match_fn_injection_override(self):
        """Caller can inject a richer match_fn (e.g. RetrieverAgent.match_score)."""
        items = [_item("python deploy", "m1")]

        def custom_match(item, tokens, q_lower):
            return 0.7

        ranked = rank_items(items, "python", field=None, match_fn=custom_match)
        assert ranked[0]["match"] == pytest.approx(0.7)
        assert ranked[0]["score"] == pytest.approx(0.7)  # * lif 1.0


# ── item_concepts / query_tokens helpers ───────────────────────────


class TestHelpers:
    def test_query_tokens_filters_empty(self):
        assert query_tokens("a  b") == ["a", "b"]
        assert query_tokens("") == []
        assert query_tokens(None) == []

    def test_item_concepts_overlaps_content(self):
        field = {"rust": 0.9, "python": 0.05}
        concepts = item_concepts(_item("rust is great"), field)
        assert concepts == ["rust"]

    def test_item_concepts_empty_without_field(self):
        assert item_concepts(_item("rust"), None) == []
        assert item_concepts(_item("rust"), {}) == []
