"""mem-service adapter — butterfly-wing LLM extraction + regex fallback (ADR-5b).

The adapter is the ingest seam that picks LLM facts when available and falls
back to the regex ``extractor`` (ADR-5) otherwise. Two layers:

1. ``extract_facts(text, providers=None)`` — the public entry. Returns an
   ``llm_provider.Extraction``. With ``providers=[]`` (or none reachable) it
   goes straight to the regex fallback (Spec §4 story 5; acceptance cmd).
2. Internally: N-way fan-out over the providers (default 3 wings = the same
   provider run 3× via prompt transforms, since CCRProvider is the only
   concrete provider today) → vote (majority per (subject,predicate,object)
   tuple) → confidence aggregation (max of wing confidences). If the voted
   result is empty or below a low-confidence floor, fall back to regex.

Regex fallback (ADR-5, upheld — not superseded): the regex ``extractor`` runs
and its facts are wrapped into ``FactOut`` triples, confidence 0.5 (determin-
istic regex, lower than a voted LLM consensus but higher than a stub).

API contract (acceptance cmd): ``adapter.extract_facts(text, providers=[])``
returns an ``Extraction`` whose ``.facts`` is non-empty for any text the regex
layer hits and whose ``.confidence`` is ≥ 0. ``Extraction`` is imported from
``llm_provider`` so callers hold one type.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

import extractor as regex_extractor
from llm_provider import (
    CCRProvider,
    Extraction,
    FactOut,
    LLMProvider,
)

# Butterfly-wing fan-out. N=3 per ADR-5b Decision. Today CCRProvider is the
# only concrete provider, so the 3 wings are the same provider under 3 prompt
# transforms (diversity via prompt variation). When more providers exist, the
# adapter fans across them; N is the wing count, not the provider count.
DEFAULT_WINGS = 3

# Below this voted confidence, distrust the LLM result and fall back to regex.
# 0.6 = a single 0.7-confidence provider (no consensus) is NOT enough; two
# agreeing wings (0.7 each, voted) clear it. Tunable knob for v3 score-tune.
FALLBACK_CONFIDENCE = 0.6

# Prompt transforms for butterfly-wing diversity. Each wraps the input text in
# a different framing; the JSON contract is identical, only the surface varies.
# ponytail: 3 hand-written transforms; a templating engine is overkill at N=3.
_WING_PROMPTS = [
    "抽取事实:",                        # bare
    "请仔细阅读并提取其中的事实三元组:",  # careful reframe
    "识别以下文本中的实体关系:",         # relation-focused reframe
]


def extract_facts(
    text: str,
    providers: list[LLMProvider] | None = None,
    wings: int = DEFAULT_WINGS,
) -> Extraction:
    """Extract facts from ``text`` via butterfly-wing LLM voting, regex fallback.

    - ``providers=[]`` or all-unreachable → regex fallback (Spec §4 story 5).
    - Otherwise: fan out ``wings`` calls across ``providers`` (round-robin when
      len(providers) < wings, else one wing per provider), vote on identical
      (subject, predicate, object) triples, aggregate confidence as the max.
    - Voted result with confidence ≥ ``FALLBACK_CONFIDENCE`` and ≥ quorum
      agreement → returned as-is.
    - Below the floor or empty: run regex fallback, then **merge** any voted
      LLM facts regex did not independently surface (dedup by
      (subject,predicate,object)). A low-confidence vote is *down-weighted,
      not discarded* — a single 0.5-confidence wing that regex missed would
      otherwise vanish silently. Final confidence = max(fb, voted);
      ``source_meta`` records ``llm_attempted`` and ``merged_voted`` count.

    The returned ``Extraction`` is never None and ``confidence`` ≥ 0.
    """
    active = [p for p in (providers or []) if _is_reachable(p)]
    if not active:
        return _regex_fallback(text)

    extractions: list[Extraction] = []
    for i in range(wings):
        provider = active[i % len(active)]
        # Apply wing-i prompt transform by re-asking with a reframed prefix.
        # CCRProvider (and any LLMProvider) gets the wing prompt inline.
        framed = f"{_WING_PROMPTS[i % len(_WING_PROMPTS)]}\n{text}"
        extractions.append(provider.extract_facts(framed))

    voted = _vote(extractions)
    if voted.facts and voted.confidence >= FALLBACK_CONFIDENCE:
        return voted

    # Low confidence or empty LLM result → regex fallback, then merge any
    # voted LLM facts regex did not surface (ADR-5b: a low-confidence vote is
    # down-weighted, not silently dropped). Dedup on the triple so a fact both
    # layers surface isn't double-counted.
    fb = _regex_fallback(text)
    existing = {(f.subject, f.predicate, f.object) for f in fb.facts}
    merged = [f for f in voted.facts
              if (f.subject, f.predicate, f.object) not in existing]
    fb.facts.extend(merged)
    fb.confidence = max(fb.confidence, voted.confidence)
    fb.source_meta["llm_attempted"] = True
    fb.source_meta["llm_confidence"] = voted.confidence
    fb.source_meta["merged_voted"] = len(merged)
    return fb


# ── voting / aggregation ──────────────────────────────────────────────

def _vote(extractions: list[Extraction]) -> Extraction:
    """Majority vote per (subject, predicate, object) triple; confidence = max.

    A triple survives if it appears in ≥ ⌈n/2⌉ wings (majority/quorum per
    ADR-5b). Confidence of the voted result is the max wing confidence among
    wings that contributed a surviving triple (max aggregation per ADR-5b:
    "confidence 聚合 max/mean" — max is the more conservative, picks the wing
    that was most sure). source_meta records wing count + agreement histogram.
    """
    n = len(extractions)
    quorum = (n + 1) // 2  # ⌈n/2⌉: 3→2, 2→1, 1→1
    triple_wings: dict[tuple[str, str, str], list[int]] = {}
    for wi, ext in enumerate(extractions):
        for f in ext.facts:
            key = (f.subject, f.predicate, f.object)
            triple_wings.setdefault(key, []).append(wi)

    surviving: list[FactOut] = []
    contributing_confidences: list[float] = []
    agree_hist: list[int] = []
    for key, wing_idxs in triple_wings.items():
        agree_hist.append(len(wing_idxs))
        if len(wing_idxs) >= quorum:
            surviving.append(FactOut(*key))
            for wi in wing_idxs:
                contributing_confidences.append(extractions[wi].confidence)

    conf = max(contributing_confidences) if contributing_confidences else 0.0
    return Extraction(
        facts=surviving, confidence=conf,
        source_meta={
            "wings": n, "quorum": quorum,
            "agreement": sorted(agree_hist, reverse=True),
            "mode": "majority",
        },
    )


# ── regex fallback (ADR-5, upheld — extractor.py unchanged) ───────────

def _regex_fallback(text: str) -> Extraction:
    """Run the ADR-5 regex extractor and lift its facts into FactOut triples.

    Confidence 0.5: deterministic regex coverage, below a voted LLM consensus
    but above a stub/empty provider. extractor.py is imported UNCHANGED per
    task scope (it stays the fallback, not superseded — ADR-5 upheld).
    """
    extracted = regex_extractor.extract(text)
    facts: list[FactOut] = []
    for f in extracted["facts"]:
        facts.append(FactOut(
            subject=f["subject"], predicate=f["predicate"], object=f["object"]))
    conf = 0.5 if facts else 0.0
    return Extraction(
        facts=facts, confidence=conf,
        source_meta={"provider": "regex", "entities": len(extracted["entities"])})


# ── provider reachability (cheap pre-check, no full call) ─────────────

def _is_reachable(provider: LLMProvider) -> bool:
    """True if ``provider`` looks usable.

    Two cheap paths, no LLM call:

    - **Has ``base_url``** (CCRProvider, LMStudioProvider, any HTTP-backed
      provider): TCP-connect the host:port from the URL. No model in the
      request → no token spend, ~ms latency. Replaces the previous
      ``extract_facts("")`` probe which fired a real LLM call per wing
      (N=3 wings = 4 billable calls at CCRProvider).
    - **No ``base_url``** (stubs, fakes): fall back to ``extract_facts("")``
      and inspect source_meta for a stub sentinel. Deterministic for fakes
      (they ignore the input); stubs self-exclude via their error string.
    """
    base_url = getattr(provider, "base_url", None)
    if base_url:
        return _tcp_reachable(base_url, timeout=2.0)
    try:
        probe = provider.extract_facts("")
    except Exception:
        return False
    err = str(probe.source_meta.get("error", ""))
    if "stub" in err or "not implemented" in err:
        return False
    return True


def _tcp_reachable(base_url: str, timeout: float = 2.0) -> bool:
    """TCP-connect the host:port of ``base_url``. Cheap reachability probe —
    no HTTP request, no model, no token spend. True if the socket opens."""
    import urllib.parse
    try:
        parsed = urllib.parse.urlparse(base_url)
    except ValueError:
        return False
    host = parsed.hostname
    if not host:
        return False
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    import socket
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


# ── default providers (cli uses this) ─────────────────────────────────

def default_providers() -> list[LLMProvider]:
    """The provider list cli.ingest uses when callers don't override. Today
    only CCRProvider; the stubs self-exclude via ``_is_reachable`` so adding
    them here is harmless (they fall back)."""
    return [CCRProvider()]


def _demo() -> None:  # ponytail self-check
    r = extract_facts("用户使用 rust", providers=[])
    assert r.facts and r.confidence >= 0, (r.facts, r.confidence)
    print("regex fallback ok:", r.facts[0])


if __name__ == "__main__":
    _demo()
