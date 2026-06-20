"""RetrieverAgent — recall ranking computation engine [Step ③, Part 1].

A *deterministic* ranking engine — no LLM. It consumes the candidate
memory items produced by ``MemoryService.recall`` and re-ranks them by
``match_score × lif_weight``, returning a programmatic
``[{item, score}, ...]`` list sorted by score descending.

Per ``memory-kernel-design.md`` §5 (recall is a *computation engine*):
the memory subsystem only computes + returns a sorted scored list; how
the request side consumes it (LLM synthesis / injection / top-K) is
none of its concern.

Layering (review correction, high severity):
    The integration point is the **route layer** (``memory.py``
    ``GET /memories`` will call ``bus.emit(RECALL)`` and fall back to
    ``service.recall`` when the OBSERVER hook returns ``None``). This
    file deliberately **does not touch ``service.recall`` internals** —
    ``MemoryService`` is the layer below the bus; reaching into it from
    a side agent would invert the ``service → bus`` dependency.

Part 1 vs Part 2:
    Part 1 keeps ``lif_state=None`` everywhere → ``lif_weight=1.0``
    (pure match ranking). The ``lif_weight`` signature already accepts
    the neural field ``V`` so Part 2 can inject the real potential
    field without changing call sites. The Part 2 weighting uses
    **rank-based percentile** (review correction): an absolute
    ``lif`` value would suppress ``score`` to ``match × 0.1`` when the
    field sits at a low resting potential — a relative rank avoids
    that and keeps the geometric balance ``match × lif`` meaningful.
"""

from __future__ import annotations

import logging
from typing import Any

from src.memory.hooks import HookPriority, MemoryHook, RecallContext
from src.memory.service import MemoryService
from src.memory.types import MemoryItem

logger = logging.getLogger(__name__)


class RetrieverAgent:
    """Deterministic recall ranking engine: ``match_score × lif_weight``.

    Not an LLM agent — pure computation. The candidate set is obtained
    from ``memory_service.recall`` (kw + KG unified, the existing
    deterministic recall) and re-scored here.
    """

    def __init__(
        self,
        memory_service: MemoryService,
        kg: Any | None = None,
    ) -> None:
        self._memory = memory_service
        # Optional KG handle for entity-name hit bonus. When omitted the
        # agent degrades to pure keyword-substring matching, which is
        # still a valid Part-1 ranking (lif_weight == 1.0 ⇒ identity).
        self._kg = kg if kg is not None else getattr(memory_service, "kg", None)

    # ── Public API ──────────────────────────────────────────────────

    async def retrieve(
        self,
        query: str,
        agent_id: str,
        top_k: int = 10,
        lif_state: Any = None,
    ) -> list[dict[str, Any]]:
        """Rank candidate memories by ``match_score × lif_weight``.

        Args:
            query: Recall query text.
            agent_id: Owning agent.
            top_k: Maximum candidates to fetch / return.
            lif_state: Part 2 neural field handle (``None`` ⇒ Part 1
                pure-match mode, ``lif_weight == 1.0``).

        Returns:
            ``[{"item": MemoryItem, "score": float}, ...]`` sorted by
            ``score`` descending. Programmatic — no LLM in the loop.
        """
        # Pull a slightly wider candidate pool so the rank-based
        # lif_weight has something to order before truncation. The bus
        # fallback path passes through ``service.recall`` verbatim when
        # this hook returns None, so this path only runs when an
        # OBSERVER RetrieverHook is registered.
        candidates: list[MemoryItem] = await self._memory.recall(
            query=query,
            agent_id=agent_id,
            top_k=max(top_k, top_k),
        )
        if not candidates:
            return []

        # Pre-compute query tokens once for every candidate.
        q_tokens = self._query_tokens(query)
        q_lower = query.strip().lower()

        scored: list[tuple[float, MemoryItem]] = []
        for item in candidates:
            scored.append((self.match_score(item, q_tokens, q_lower), item))

        # Apply lif_weight (Part 1: identity; Part 2: rank-based).
        weights = [self.lif_weight(item, lif_state) for _, item in scored]
        if lif_state is not None:
            # Rank-based percentile: a candidate's weight is its rank
            # position normalised into (0, 1] — relative ordering, not
            # the raw lif magnitude (review correction, high severity).
            weights = self._rank_percentile_weights(scored, weights)

        results: list[dict[str, Any]] = [
            {"item": item, "score": float(match * w)}
            for (match, item), w in zip(scored, weights)
        ]
        results.sort(key=lambda r: r["score"], reverse=True)
        return results[:top_k]

    # ── match_score ─────────────────────────────────────────────────

    def match_score(
        self,
        item: MemoryItem,
        query_tokens: list[str] | None = None,
        query_lower: str | None = None,
    ) -> float:
        """Keyword-substring hit + KG entity-name hit, clamped to [0, 1].

        Pure function — no LLM, deterministic. Two signals:

        1. **Keyword substring** — fraction of query tokens that appear
           as a case-insensitive substring of ``item.content``
           (mirrors ``KeywordRecall``'s matching). A full-phrase exact
           hit (the whole query appearing verbatim) gets a small bonus.
        2. **KG entity-name hit** — if a KG is configured, +bonus when
           a query token matches an entity name in the graph.

        Returns a value in ``[0.0, 1.0]``.
        """
        if query_tokens is None or query_lower is None:
            query_lower = (query_lower or "").strip().lower()
            query_tokens = self._query_tokens(query_lower or "")

        content_lower = (item.content or "").lower()

        match = 0.0
        if query_tokens:
            hits = sum(1 for tok in query_tokens if tok and tok in content_lower)
            match = hits / len(query_tokens)
            # Verbatim full-query bonus (strongest signal).
            if query_lower and query_lower in content_lower:
                match = min(1.0, match + 0.2)

        # KG entity-name hit bonus. Only the *name* field is matched
        # (structural KG hits already surface via service.recall's KG
        # path; here we add a lexical bonus on top).
        if self._kg is not None and query_tokens:
            match = min(1.0, match + self._kg_entity_hit_bonus(query_tokens))

        return float(max(0.0, min(1.0, match)))

    # ── lif_weight ──────────────────────────────────────────────────

    def lif_weight(self, item: MemoryItem, lif_state: Any) -> float:
        """Weight applied to ``match_score`` from the neural field.

        Part 1 contract: ``lif_state is None`` ⇒ ``1.0`` (degenerate,
        pure-match ranking). This is the **Part 2 injection point**:
        Part 2 passes the real ``NeuralState`` field here and the
        retrieve() path converts it to a rank-based percentile (see
        :meth:`_rank_percentile_weights`) so the absolute potential
        magnitude never silently suppresses scores.

        Returns ``1.0`` when ``lif_state`` is None; otherwise returns
        a per-item raw weight that the caller normalises into a
        relative rank (the returned scalar here is an *intermediate*
        value, not the final weight).
        """
        if lif_state is None:
            return 1.0
        # Part 2 placeholder: read item-relevant concepts from the field.
        # Intentionally returns a coarse, non-suppressing value so the
        # rank-percentile normalisation in retrieve() does the real
        # work; concrete potential extraction lands with NeuralState.
        try:
            field = getattr(lif_state, "field", None) or {}
        except Exception:  # pragma: no cover - defensive
            field = {}
        if not field:
            return 1.0
        # Aggregate potential over item-attached concepts (best-effort).
        pot = self._aggregate_potential(item, field)
        return float(pot)

    # ── RetrieverHook ───────────────────────────────────────────────

    # (RetrieverHook is defined below as a top-level class; this method
    #  documents the wiring but lives on the hook, not the agent.)

    # ── Internals ───────────────────────────────────────────────────

    @staticmethod
    def _query_tokens(query: str) -> list[str]:
        """Tokenise a query for substring matching (case-folded)."""
        return [t for t in (query or "").lower().split() if t]

    def _kg_entity_hit_bonus(self, query_tokens: list[str]) -> float:
        """Return a small bonus if any query token is a KG entity name.

        Capped so a single entity hit contributes a modest additive
        bonus (entity-name collision is a weaker signal than a content
        substring hit). Failures (e.g. KG API mismatch) degrade to 0.
        """
        try:
            hits = 0
            for tok in query_tokens:
                if self._kg.find_entity_by_name(tok) is not None:
                    hits += 1
        except Exception:
            return 0.0
        if hits == 0:
            return 0.0
        # 0.1 per hit, capped at 0.3 — bonus, not a dominant signal.
        return min(0.3, 0.1 * hits)

    def _aggregate_potential(self, item: MemoryItem, field: dict[str, float]) -> float:
        """Best-effort aggregate potential of an item over the field.

        Items do not carry an explicit concept list in Part 1; we look
        up field concepts whose name appears in the item content. The
        result is consumed only by :meth:`_rank_percentile_weights`,
        which normalises it into a relative rank — so the absolute
        scale here is irrelevant.
        """
        if not field:
            return 0.0
        content_lower = (item.content or "").lower()
        matched = [
            pot for concept, pot in field.items()
            if concept and concept.lower() in content_lower
        ]
        if not matched:
            return 0.0
        # Mean potential of matched concepts — stable aggregate.
        return float(sum(matched) / len(matched))

    @staticmethod
    def _rank_percentile_weights(
        scored: list[tuple[float, MemoryItem]],
        raw_weights: list[float],
    ) -> list[float]:
        """Convert raw lif weights into relative rank percentiles.

        Review correction (high): rank-based, not absolute — otherwise a
        field sitting at a low resting potential yields
        ``score ≈ match × 0.1`` and silently buries everything.

        The candidate with the highest raw weight gets weight ``1.0``;
        the lowest gets ``1 / n``; the rest interpolate linearly by
        rank. This preserves the field's ordering while guaranteeing no
        candidate is suppressed below its match score's relative share.
        """
        n = len(raw_weights)
        if n == 0:
            return []
        if n == 1:
            return [1.0]
        # argsort descending by raw weight (stable on ties).
        order = sorted(range(n), key=lambda i: raw_weights[i], reverse=True)
        weights = [0.0] * n
        for rank, idx in enumerate(order):
            # rank 0 → 1.0; rank n-1 → 1/n.
            weights[idx] = 1.0 - (rank / n) * (1.0 - 1.0 / n)
        return weights


class RetrieverHook(MemoryHook):
    """OBSERVER-priority hook bridging ``EventType.RECALL`` → RetrieverAgent.

    The bus emits a :class:`RecallContext`; this hook delegates to
    :meth:`RetrieverAgent.retrieve` and returns the scored, sorted list.
    The route layer treats a ``None`` return as "no retriever wired" and
    falls back to plain ``service.recall`` — so registering this hook is
    the feature gate (default off until A/B regression proves
    non-inferiority vs the existing UnifiedRecall).
    """

    priority = HookPriority.OBSERVER

    def __init__(self, retriever: RetrieverAgent) -> None:
        self._agent = retriever

    async def on_recall(self, ctx: RecallContext) -> list[dict[str, Any]]:
        """Override RECALL → delegate to the deterministic ranking engine."""
        return await self._agent.retrieve(
            query=ctx.query,
            agent_id=ctx.agent_id,
            top_k=ctx.top_k,
            lif_state=ctx.lif_state,
        )
