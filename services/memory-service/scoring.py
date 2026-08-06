"""mem-service scoring — ``scored = match × lif`` (ADR-4).

Pure functions. ``match_item`` substring-hit logic is lifted verbatim from
``weighted_recall.py:54-88`` (AO2), but operates on ``Fact.value`` (str) instead
of ``MemoryItem.content`` (the MemoryItem layer does not exist here — ADR-2).

``lif`` reads the ``Fact.LIF`` storage scalar ``∈ [0,1]`` directly (ADR-4):
decoupled from AO2's runtime NeuralField rank-based percentile, which is a
relative field ranking that does not apply to a static trust scalar.

No LLM, unit-testable in isolation.
"""

from __future__ import annotations

from typing import Any


def query_tokens(query: str) -> list[str]:
    """Case-folded tokenization (mirrors weighted_recall.query_tokens)."""
    return [t for t in (query or "").lower().split() if t]


def match_item(
    content: str | None,
    query_tokens: list[str] | None = None,
    query_lower: str | None = None,
) -> float:
    """``match_item(query, content)`` — substring-hit ratio of query vs content, ``∈ [0, 1]``.

    Lifted verbatim from ``weighted_recall.match_item`` (:54-88) but ``content``
    is a ``str`` (``Fact.value``) instead of ``MemoryItem.content``. The
    MemoryItem layer does not exist in mem-service (ADR-2: Fact reification is
    self-contained), so the field-access ``item.content`` becomes a plain arg.

    Args:
        content: Text to score against (``Fact.value``).
        query_tokens: Pre-tokenized query (optional; derived from query_lower if None).
        query_lower: Lowercased full query (optional; verbatim-phrase hit bonus).

    Returns:
        Match score in ``[0.0, 1.0]``.
    """
    if query_tokens is None:
        query_lower = (query_lower or "").strip().lower()
        query_tokens = [t for t in query_lower.split() if t]

    content_lower = (content or "").lower()

    if not query_tokens:
        return 0.0

    hits = sum(1 for tok in query_tokens if tok and tok in content_lower)
    match = hits / len(query_tokens)
    # verbatim 全句命中加成（最强信号）
    if query_lower and query_lower in content_lower:
        match = min(1.0, match + 0.2)
    return float(max(0.0, min(1.0, match)))


def score_fact(fact: dict[str, Any], query: str) -> dict[str, Any]:
    """Score one Fact: ``scored = match × lif`` where ``lif = fact["LIF"]``.

    Args:
        fact: Decoded Fact dict (must carry ``value`` and ``LIF`` keys).
        query: Recall query text.

    Returns:
        ``{"fact": fact, "match": float, "lif": float, "score": float}``.
    """
    q_lower = (query or "").strip().lower()
    q_tokens = query_tokens(q_lower)
    m = match_item(fact.get("value"), q_tokens, q_lower)
    # ponytail: lif reads the Fact.LIF storage scalar directly (ADR-4);
    # not AO2 NeuralField rank-based percentile — that is a runtime field
    # ranking, wrong for a static trust scalar. No centrality/vec (MVP).
    lif = float(fact.get("LIF") or 0.0)
    return {"fact": fact, "match": m, "lif": lif, "score": float(m * lif)}


__all__ = ["query_tokens", "match_item", "score_fact"]
