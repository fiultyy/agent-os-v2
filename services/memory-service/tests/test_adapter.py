"""Node Adapter — adapter butterfly-wing LLM extraction + regex fallback (ADR-5b).

Two paths exercised:

1. **mock provider** (LLM path): a fake ``LLMProvider`` returns facts with
   confidence above the quorum floor; the adapter votes N=3 wings, aggregates
   confidence (max), stamps facts as the provider's. cli stamps extractor="llm".
2. **regex fallback**: ``providers=[]`` (no provider) → adapter goes straight
   to the ADR-5 regex extractor; facts survive for text the regex hits (e.g.
   "用户使用 rust"); cli stamps extractor="regex".

Plus an **integration** test against the deployed ccr router at 127.0.0.1:3456,
``skipif`` the router is not reachable (so CI / offline runs don't fail).

Acceptance cmd: ``cd services/memory-service && python -m pytest tests/test_adapter.py -q``.
"""

from __future__ import annotations

import os
import sys
import urllib.request

import pytest

# Make the service package importable as top-level modules (cli, adapter, ...)
# regardless of pytest's invocation cwd.
_SRV_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _SRV_DIR not in sys.path:
    sys.path.insert(0, _SRV_DIR)

import adapter  # noqa: E402
import cli  # noqa: E402
import db  # noqa: E402
import store  # noqa: E402
from llm_provider import CCRProvider, Extraction, FactOut  # noqa: E402


# ── fixtures ─────────────────────────────────────────────────────────

@pytest.fixture()
def fresh_db(tmp_path):
    """Per-test isolated SQLite file; resets db's cached connection."""
    db_path = tmp_path / "memory.db"
    db.init(str(db_path))
    yield db_path


class _FakeProvider:
    """Fake LLMProvider: always returns the same facts at a set confidence.

    Implements the LLMProvider Protocol structurally (duck-typed; no inherit).
    Used to drive the LLM vote path deterministically without a network.
    """
    def __init__(self, facts: list[FactOut], confidence: float = 0.9):
        self._facts = facts
        self._confidence = confidence

    def extract_facts(self, text: str) -> Extraction:
        return Extraction(
            facts=list(self._facts), confidence=self._confidence,
            source_meta={"provider": "fake"})


# ── regex fallback path (providers=[]) ───────────────────────────────

def test_extract_facts_empty_providers_falls_back_to_regex():
    """Spec §4 story 5: ``providers=[]`` → regex fallback (ADR-5 upheld).
    Text the regex layer hits must surface facts with confidence ≥ 0."""
    r = adapter.extract_facts("用户使用 rust", providers=[])
    assert r.facts, "regex fallback should hit '用户 uses rust'"
    assert r.confidence >= 0
    assert r.source_meta.get("provider") == "regex"
    # The fact triple the regex layer is documented to produce here.
    triples = {(f.subject, f.predicate, f.object) for f in r.facts}
    assert ("用户", "uses", "rust") in triples


def test_extract_facts_regex_fallback_empty_on_no_match():
    """Regex fallback on text it can't parse (pure Chinese bare sentence with
    no predicate) → empty facts, confidence 0.0 — never raises."""
    r = adapter.extract_facts("今天天气真不错", providers=[])
    assert r.facts == []
    assert r.confidence == 0.0


# ── LLM vote path (mock provider) ────────────────────────────────────

def test_extract_facts_llm_majority_vote_aggregates_max_confidence():
    """N=3 wings of a mock provider that consistently returns the same facts:
    the fact survives quorum (≥2/3), confidence = max wing confidence."""
    fact = FactOut(subject="用户", predicate="uses", object="实证方法")
    provider = _FakeProvider([fact], confidence=0.9)
    r = adapter.extract_facts("用户偏好实证", providers=[provider], wings=3)
    assert r.facts, "voted LLM fact should survive"
    assert (r.facts[0].subject, r.facts[0].predicate, r.facts[0].object) == (
        "用户", "uses", "实证方法")
    # max aggregation: 0.9 from each agreeing wing → 0.9.
    assert r.confidence == 0.9
    assert r.source_meta.get("wings") == 3
    assert r.source_meta.get("mode") == "majority"


def test_extract_facts_low_confidence_merges_voted_facts():
    """ADR-5b: a voted fact below the fallback floor is **merged**, not
    silently dropped. Provider returns a fact regex can't hit, at confidence
    0.5 (below FALLBACK_CONFIDENCE 0.6) → voted.confidence 0.5 < 0.6 → regex
    fallback runs AND merges the voted X-uses-Y back in (regex misses it).
    Without the merge, the voted fact would vanish silently."""
    fact = FactOut(subject="X", predicate="uses", object="Y")
    provider = _FakeProvider([fact], confidence=0.5)
    r = adapter.extract_facts("用户使用 rust", providers=[provider], wings=1)
    # quorum=1 → voted fact survives; confidence 0.5 < 0.6 → fallback + merge.
    assert r.source_meta.get("provider") == "regex"
    assert r.source_meta.get("llm_attempted") is True
    assert r.source_meta.get("merged_voted") == 1
    triples = {(f.subject, f.predicate, f.object) for f in r.facts}
    # regex surface + the merged LLM fact both present.
    assert ("用户", "uses", "rust") in triples
    assert ("X", "uses", "Y") in triples
    # merged confidence = max(regex 0.5, voted 0.5) = 0.5.
    assert r.confidence == 0.5


def test_extract_facts_low_confidence_dedups_overlap():
    """Merge dedups on (subject,predicate,object): a voted fact regex also
    surfaces is not double-counted, and merged_voted counts only the net-new
    facts the LLM contributed."""
    # Same fact regex will surface ("用户 uses rust") at low LLM confidence.
    fact = FactOut(subject="用户", predicate="uses", object="rust")
    provider = _FakeProvider([fact], confidence=0.5)
    r = adapter.extract_facts("用户使用 rust", providers=[provider], wings=1)
    triples = {(f.subject, f.predicate, f.object) for f in r.facts}
    # Single (用户,uses,rust), no dup; merged_voted=0 (regex already had it).
    assert triples == {("用户", "uses", "rust")}
    assert r.source_meta.get("merged_voted") == 0


def test_extract_facts_quorum_drops_minority_triples():
    """A triple appearing in only 1 of 3 wings (< quorum 2) is dropped."""
    # Three providers, each returns a different fact → no triple reaches 2/3.
    p1 = _FakeProvider([FactOut("a", "uses", "b")], 0.9)
    p2 = _FakeProvider([FactOut("c", "uses", "d")], 0.9)
    p3 = _FakeProvider([FactOut("e", "uses", "f")], 0.9)
    r = adapter.extract_facts(
        "用户使用 rust", providers=[p1, p2, p3], wings=3)
    # No LLM triple survives quorum → low voted confidence → regex fallback.
    assert r.source_meta.get("provider") == "regex"


# ── cli wiring ───────────────────────────────────────────────────────

def test_cli_ingest_regex_path_stamps_extractor_regex(fresh_db):
    """cli.ingest with providers=[] stamps fact.extractor="regex"."""
    summary = cli.ingest("用户使用 rust", providers=[])
    assert summary["facts"], "should persist at least one fact"
    for fid in summary["facts"]:
        f = store.get_fact(fid)
        assert f["extractor"] == "regex"


def test_cli_ingest_llm_path_stamps_extractor_llm(fresh_db):
    """cli.ingest with a high-confidence mock provider stamps extractor="llm"."""
    fact = FactOut(subject="用户", predicate="uses", object="实证方法")
    provider = _FakeProvider([fact], confidence=0.9)
    summary = cli.ingest("用户偏好实证", providers=[provider])
    assert summary["facts"]
    for fid in summary["facts"]:
        f = store.get_fact(fid)
        assert f["extractor"] == "llm"
    # Subject entity was lazily created from the fact.
    assert store.find_entities_by_name("用户")


def test_cli_ingest_default_providers_is_ccr(fresh_db):
    """cli.ingest with no ``providers`` arg uses [CCRProvider()]. When ccr is
    unreachable this falls back to regex; when reachable it uses LLM. Either
    way it must not raise and must persist facts for regex-hittable text."""
    summary = cli.ingest("用户使用 rust")
    assert summary["facts"]


# ── ccr integration (skipif unreachable) ─────────────────────────────

def _ccr_reachable() -> bool:
    """True if the ccr router at 127.0.0.1:3456 answers."""
    try:
        urllib.request.urlopen(
            "http://127.0.0.1:3456/", timeout=2)
    except Exception:
        return False
    return True


@pytest.mark.skipif(not _ccr_reachable(), reason="ccr router not running")
def test_ccr_provider_live_extraction():
    """Live: CCRProvider against the deployed router extracts a fact from a
    Chinese bare sentence the regex layer cannot parse."""
    r = CCRProvider().extract_facts("项目使用 rust 做后端开发")
    # Live call may return 0–N facts depending on model; we only assert the
    # contract (Extraction shape, confidence ∈ [0,1], no raise).
    assert isinstance(r, Extraction)
    assert 0.0 <= r.confidence <= 1.0
    for f in r.facts:
        assert f.subject and f.predicate and f.object


@pytest.mark.skipif(not _ccr_reachable(), reason="ccr router not running")
def test_adapter_ccr_butterfly_wing_live():
    """Live: adapter.extract_facts with [CCRProvider()] runs the N=3 wing fan-
    out against the live router and returns a voted Extraction (or regex
    fallback if the vote is low-confidence)."""
    r = adapter.extract_facts(
        "项目使用 rust 做后端开发", providers=[CCRProvider()], wings=3)
    assert isinstance(r, Extraction)
    assert r.confidence >= 0.0
