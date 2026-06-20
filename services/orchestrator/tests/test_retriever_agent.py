"""Tests for Step ③ RetrieverAgent — deterministic recall ranking engine.

Covers the three required cases:
- ``match_score`` (keyword substring + KG entity-name hit, clamped [0,1])
- Part 1 degenerate mode (``lif_state=None`` ⇒ ``lif_weight=1.0`` ⇒
  ranking equals match_score order)
- Part 2 lif injection (rank-based percentile re-weights, no absolute
  suppression)

Plus ``RetrieverHook`` delegation and the non-LLM contract.
"""

import os
import sys
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

try:
    import pysqlite3

    sys.modules["sqlite3"] = pysqlite3
    sys.modules["sqlite3.dbapi2"] = pysqlite3.dbapi2
except ImportError:
    pass

import pytest

from src.memory.hooks import RecallContext
from src.memory.sideline.retriever_agent import RetrieverAgent, RetrieverHook
from src.memory.types import MemoryItem


# ── Fixtures ────────────────────────────────────────────────────────


def _item(content: str, mid: str = "") -> MemoryItem:
    return MemoryItem(id=mid or content[:8], content=content, agent_id="a")


class _FakeRecallService:
    """Stand-in for MemoryService — returns a canned candidate list.

    The agent only calls ``.recall(...)`` on it; no store needed, which
    keeps these tests free of SQLite/KG plumbing (the KG bonus path is
    exercised separately with a real KnowledgeGraph).
    """

    def __init__(self, candidates: list[MemoryItem]) -> None:
        self._candidates = candidates
        self.kg = None  # agent falls back to None ⇒ pure keyword path

    async def recall(self, query: str = "", agent_id: str = "", top_k: int = 10, **kw: Any) -> list[MemoryItem]:
        return list(self._candidates[:top_k])


class _FakeKG:
    """Minimal KG stub exposing only ``find_entity_by_name``."""

    def __init__(self, names: set[str]) -> None:
        self._names = {n.lower() for n in names}

    def find_entity_by_name(self, name: str) -> dict[str, Any] | None:
        if name.lower() in self._names:
            return {"name": name}
        return None


class _FakeLifState:
    """Stand-in for Part 2 NeuralState with a ``field`` attribute."""

    def __init__(self, field: dict[str, float]) -> None:
        self.field = field


# ── match_score ────────────────────────────────────────────────────


class TestMatchScore:
    def test_no_overlap_is_zero(self) -> None:
        agent = RetrieverAgent(_FakeRecallService([]))
        score = agent.match_score(_item("hello world"), ["python"], "python")
        assert score == 0.0

    def test_partial_token_hit(self) -> None:
        agent = RetrieverAgent(_FakeRecallService([]))
        # 1 of 2 query tokens present ⇒ 0.5
        score = agent.match_score(
            _item("deploy with python"), ["deploy", "rust"], "deploy rust",
        )
        assert score == pytest.approx(0.5)

    def test_all_tokens_hit(self) -> None:
        agent = RetrieverAgent(_FakeRecallService([]))
        score = agent.match_score(
            _item("deploy rust service"), ["deploy", "rust"], "deploy rust",
        )
        # All tokens hit (1.0) + verbatim full-query bonus (+0.2) ⇒ clamped 1.0
        assert score == 1.0

    def test_is_case_insensitive(self) -> None:
        agent = RetrieverAgent(_FakeRecallService([]))
        score = agent.match_score(
            _item("PYTHON is great"), ["python"], "python",
        )
        assert score > 0.0

    def test_clamped_to_unit_interval(self) -> None:
        agent = RetrieverAgent(_FakeRecallService([]))
        # All tokens + verbatim + a KG entity bonus must not exceed 1.0.
        kg = _FakeKG({"python"})
        agent._kg = kg  # type: ignore[attr-defined]
        score = agent.match_score(
            _item("python python python"), ["python"], "python",
        )
        assert 0.0 <= score <= 1.0
        assert score == 1.0

    def test_kg_entity_bonus_only_when_kg_configured(self) -> None:
        item = _item("nothing relevant here")
        no_kg = RetrieverAgent(_FakeRecallService([]))
        with_kg = RetrieverAgent(_FakeRecallService([]))
        with_kg._kg = _FakeKG({"python"})  # type: ignore[attr-defined]

        assert no_kg.match_score(item, ["python"], "python") == 0.0
        # Entity name matches even though content has no overlap.
        assert with_kg.match_score(item, ["python"], "python") == pytest.approx(0.1)


# ── Part 1: lif_state=None ⇒ lif_weight=1.0, ranking == match order ─


class TestPart1Degenerate:
    @pytest.mark.asyncio
    async def test_lif_weight_is_identity_when_none(self) -> None:
        agent = RetrieverAgent(_FakeRecallService([]))
        assert agent.lif_weight(_item("x"), None) == 1.0

    @pytest.mark.asyncio
    async def test_ranking_equals_match_order(self) -> None:
        cands = [
            _item("python deploy pipeline"),  # 2/2 hit + verbatim
            _item("rust memory kernel"),      # 1/3 hit
            _item("deploy notes only"),       # 1/2 hit
        ]
        agent = RetrieverAgent(_FakeRecallService(cands))
        ranked = await agent.retrieve(query="deploy python", agent_id="a", top_k=3)

        # Score = match * 1.0; top candidate is the all-token + verbatim one.
        assert ranked[0]["item"].content == "python deploy pipeline"
        assert ranked[0]["score"] == pytest.approx(1.0)
        # Scores monotonically non-increasing.
        scores = [r["score"] for r in ranked]
        assert scores == sorted(scores, reverse=True)

    @pytest.mark.asyncio
    async def test_respects_top_k(self) -> None:
        cands = [_item(f"python item {i}") for i in range(5)]
        agent = RetrieverAgent(_FakeRecallService(cands))
        ranked = await agent.retrieve(query="python", agent_id="a", top_k=2)
        assert len(ranked) == 2

    @pytest.mark.asyncio
    async def test_empty_candidates_returns_empty(self) -> None:
        agent = RetrieverAgent(_FakeRecallService([]))
        ranked = await agent.retrieve(query="anything", agent_id="a")
        assert ranked == []


# ── Part 2: lif injection (rank-based percentile) ─────────────────


class TestLifInjection:
    @pytest.mark.asyncio
    async def test_lif_state_none_passes_through_identity(self) -> None:
        # field present but lif_state=None at call site → identity.
        agent = RetrieverAgent(_FakeRecallService([]))
        assert agent.lif_weight(_item("x"), None) == 1.0

    @pytest.mark.asyncio
    async def test_rank_percentile_preserves_order_no_suppression(self) -> None:
        # Two items with equal match; the one whose content touches a
        # higher-potential concept must rank first.
        cands = [
            _item("rust language primer"),   # touches "rust" (high pot)
            _item("python language primer"),  # touches "python" (low pot)
        ]
        svc = _FakeRecallService(cands)
        agent = RetrieverAgent(svc)
        lif = _FakeLifState(field={"rust": 0.9, "python": 0.05})

        ranked = await agent.retrieve(
            query="language", agent_id="a", top_k=2, lif_state=lif,
        )
        # Both have the same match score (1/1 token "language" present);
        # lif rank reorders them: rust item first.
        assert ranked[0]["item"].content == "rust language primer"
        assert ranked[0]["score"] > ranked[1]["score"]
        # No absolute suppression: top weight is 1.0, so the winner keeps
        # its full match score.
        match_top = agent.match_score(ranked[0]["item"], ["language"], "language")
        assert ranked[0]["score"] == pytest.approx(match_top * 1.0)

    @pytest.mark.asyncio
    async def test_lif_field_empty_degrades_to_identity(self) -> None:
        cands = [_item("python deploy"), _item("rust deploy")]
        agent = RetrieverAgent(_FakeRecallService(cands))
        lif = _FakeLifState(field={})  # empty field ⇒ no re-rank signal
        ranked = await agent.retrieve(
            query="deploy", agent_id="a", top_k=2, lif_state=lif,
        )
        # With empty field, lif_weight returns 1.0 for all → match-only order.
        assert all(r["score"] > 0.0 for r in ranked)

    @pytest.mark.asyncio
    async def test_single_candidate_weight_is_one(self) -> None:
        agent = RetrieverAgent(_FakeRecallService([]))
        weights = agent._rank_percentile_weights([(0.5, _item("x"))], [0.42])
        assert weights == [1.0]


# ── RetrieverHook delegation ──────────────────────────────────────


class TestRetrieverHook:
    @pytest.mark.asyncio
    async def test_hook_priority_is_observer(self) -> None:
        from src.memory.hooks import HookPriority

        agent = RetrieverAgent(_FakeRecallService([]))
        hook = RetrieverHook(agent)
        assert hook.priority == HookPriority.OBSERVER

    @pytest.mark.asyncio
    async def test_on_recall_delegates_to_agent(self) -> None:
        cands = [_item("python deploy"), _item("rust notes")]
        agent = RetrieverAgent(_FakeRecallService(cands))
        hook = RetrieverHook(agent)

        ctx = RecallContext(query="python", agent_id="a", top_k=2)
        result = await hook.on_recall(ctx)

        # The engine ranks all candidates (it does not drop zero-score
        # ones — that is the request side's job). The matching item
        # sorts first with a positive score; the non-match sorts last.
        assert isinstance(result, list)
        assert result[0]["item"].content == "python deploy"
        assert result[0]["score"] > 0.0
        assert result[-1]["score"] == 0.0

    @pytest.mark.asyncio
    async def test_on_recall_forwards_lif_state(self) -> None:
        cands = [_item("rust language primer"), _item("python language primer")]
        agent = RetrieverAgent(_FakeRecallService(cands))
        hook = RetrieverHook(agent)

        ctx = RecallContext(
            query="language",
            agent_id="a",
            top_k=2,
            lif_state=_FakeLifState(field={"rust": 0.9, "python": 0.05}),
        )
        result = await hook.on_recall(ctx)
        assert result[0]["item"].content == "rust language primer"


# ── Non-LLM contract ──────────────────────────────────────────────


class TestNoLLM:
    @pytest.mark.asyncio
    async def test_agent_has_no_llm_attribute(self) -> None:
        agent = RetrieverAgent(_FakeRecallService([]))
        # Pure computation engine — no LLM client wired.
        assert not hasattr(agent, "_llm")
        assert not hasattr(agent, "llm")

    @pytest.mark.asyncio
    async def test_retrieve_does_not_invoke_llm(self) -> None:
        # If any LLM call were attempted, this would fail since the
        # service stub has no chat method.
        cands = [_item("python deploy")]
        agent = RetrieverAgent(_FakeRecallService(cands))
        ranked = await agent.retrieve(query="python", agent_id="a")
        assert len(ranked) == 1
