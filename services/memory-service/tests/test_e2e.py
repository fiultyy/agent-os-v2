"""Node F — closed-loop e2e: ingest → recall → hit + ordering + schema join.

Drives the top seams end-to-end against an isolated per-test SQLite file:

- ``cli.ingest`` (Node B) extracts entities+facts via the regex EntityExtractor
  and persists them; ``cli.recall`` (Spec §6 seam) navigates the KG, scores
  ``match × lif`` (ADR-4) and returns Facts ordered desc.
- Schema cross-table join consistency: every returned Fact's ``subject_id`` /
  ``object_id`` resolves to a row in ``entity`` (FK holds), and Fact columns
  match the ADR-2/3 schema.
- No decay assertions (type-aware LIF decay deferred — ADR-6, task scope).

Acceptance cmd: ``cd services/memory-service && python -m pytest tests/test_e2e.py -q``.
"""

from __future__ import annotations

import os
import sys

import pytest

# Make the service package importable as top-level modules (cli, db, store, ...)
# regardless of pytest's invocation cwd.
_SRV_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _SRV_DIR not in sys.path:
    sys.path.insert(0, _SRV_DIR)

import cli  # noqa: E402
import db  # noqa: E402
import store  # noqa: E402


@pytest.fixture()
def fresh_db(tmp_path):
    """Per-test isolated SQLite file; resets db's cached connection.

    db.init() switches the active connection when db_path differs, so each
    test starts from a clean schema with deterministic state.
    """
    db_path = tmp_path / "memory.db"
    db.init(str(db_path))
    yield db_path


# ── helpers ────────────────────────────────────────────────────────────

def _fact_values(facts):
    return [f["value"] for f in facts]


def _assert_schema_join(facts):
    """Every Fact joins back to entity: subject_id always resolves, object_id
    resolves when present. Columns carry the ADR-2/3 contract."""
    for f in facts:
        assert f["status"] == "active"
        # subject_id is NOT NULL in schema (FK → entity.id).
        subj = store.get_entity(f["subject_id"])
        assert subj is not None, f"Fact.subject_id {f['subject_id']} dangling"
        # object_id nullable; when present it must resolve.
        if f["object_id"]:
            obj = store.get_entity(f["object_id"])
            assert obj is not None, f"Fact.object_id {f['object_id']} dangling"
        # ADR-4: LIF is a [0,1] storage scalar (not NeuralField rank).
        assert 0.0 <= f["LIF"] <= 1.0
        # ADR-5: facts ingested via the regex cli carry extractor="regex".
        assert f["extractor"] == "regex"


# ── closed loop: ingest → recall (literal hit) ────────────────────────

def test_ingest_then_recall_literal_hit(fresh_db):
    """User Story 6: ingest '用户使用 rust 进行开发' → recall 'rust' returns
    Fact(subject=用户, predicate=uses, object=rust). Literal/substring hit."""
    summary = cli.ingest("用户使用 rust 进行开发")
    assert summary["facts"], "ingest produced no facts"
    assert summary["entities"] >= 1

    hits = cli.recall("rust")
    assert hits, "recall returned no facts for a literal hit"
    assert any(h["value"] == "rust" for h in hits), _fact_values(hits)

    the_fact = next(h for h in hits if h["value"] == "rust")
    subj = store.get_entity(the_fact["subject_id"])
    assert subj["name"] == "用户"
    assert the_fact["predicate"] == "uses"
    _assert_schema_join(hits)


def test_multi_ingest_recall_each_query(fresh_db):
    """Several ingests → each query recalls its own fact (literal hit, no
    cross-talk). The closed loop is the Node F deliverable.

    ADR-4: match is a substring hit on Fact.value (the content carrier), so
    queries target value tokens ('rust'/'Pydantic'/'笔记'), not subject names.
    """
    cli.ingest("用户使用 rust 进行开发")            # value='rust'
    cli.ingest("FastAPI uses Pydantic.")            # value='Pydantic'
    cli.ingest("Logseq 是笔记工具")                 # value='笔记工具'

    rust = cli.recall("rust")
    pyd = cli.recall("Pydantic")
    log = cli.recall("笔记")

    assert any("rust" == h["value"] for h in rust), _fact_values(rust)
    assert any("Pydantic" == h["value"] for h in pyd), _fact_values(pyd)
    assert any(h["predicate"] == "is_a" for h in log), log

    # No cross-talk: 'rust' query should not surface the Pydantic/Logseq facts
    # (literal value match is the v1 signal — ADR-4).
    assert "Pydantic" not in _fact_values(rust)

    _assert_schema_join(rust + pyd + log)


# ── ordering: scored = match × lif (ADR-4) ─────────────────────────────

def test_recall_orders_by_match_times_lif(fresh_db):
    """Two facts both literal-hit the same query token but carry different LIF;
    the higher-LIF fact ranks first (match equal ⇒ score ∝ LIF). cli.ingest
    stamps LIF=0.5 always, so seed one fact directly via store to vary LIF."""
    # Entity + a cli-ingested fact (LIF=0.5 default).
    cli.ingest("用户使用 rust 进行开发")
    alice = store.put_entity("Alice", "person")
    # Higher-LIF fact whose value also contains 'rust' ⇒ match equal, LIF up.
    store.put_fact(
        alice, "uses", value="rust for backend",
        LIF=0.9, extractor="regex",
    )

    hits = cli.recall("rust")
    assert len(hits) >= 2, [h["value"] for h in hits]
    # Both literal-hit 'rust' ⇒ higher LIF ranks first.
    assert hits[0]["LIF"] >= hits[1]["LIF"]
    assert hits[0]["value"] == "rust for backend"

    # Verbose path exposes match/lif/score (debug surface, Spec §4 story 3).
    detail = cli.recall("rust", verbose=True)
    assert all("score" in d and "match" in d and "lif" in d for d in detail)
    scores = [d["score"] for d in detail]
    assert scores == sorted(scores, reverse=True), scores
    # score == match × lif (ADR-4 literal).
    for d in detail:
        assert abs(d["score"] - d["match"] * d["lif"]) < 1e-9
    _assert_schema_join([d["fact"] for d in detail])


def test_recall_zero_lif_filtered(fresh_db):
    """ADR-4: score = match × lif. A literal-hit fact with LIF=0 scores 0 and
    is dropped (recall surfaces hits, not zero-score noise)."""
    cli.ingest("用户使用 rust 进行开发")  # LIF=0.5 default — survives
    bob = store.put_entity("Bob", "person")
    store.put_fact(bob, "uses", value="rust buried", LIF=0.0, extractor="regex")

    hits = cli.recall("rust")
    # The LIF=0 fact is filtered (score=0); the LIF=0.5 cli fact remains.
    assert all(h["LIF"] > 0.0 for h in hits), [h["LIF"] for h in hits]
    assert any(h["value"] == "rust" for h in hits)


# ── schema cross-table join consistency ────────────────────────────────

def test_schema_fact_entity_join_consistent(fresh_db):
    """Every Fact returned joins cleanly to entity (FK integrity); the ADR-2/3
    content-carrier columns are present and well-formed. Cross-table
    consistency = Node F scope."""
    cli.ingest("用户使用 rust 进行开发")
    cli.ingest("FastAPI uses Pydantic.")
    cli.ingest("Logseq 是笔记工具")

    # ADR-3 content-carrier + identity columns every Fact must carry.
    required_cols = {
        "id", "subject_id", "predicate", "object_id", "value",
        "LIF", "extractor", "status", "created_at",
    }
    for q in ("rust", "Pydantic", "笔记"):
        for f in cli.recall(q):
            assert required_cols <= set(f.keys()), required_cols - set(f.keys())
            _assert_schema_join([f])
            # Cross-table: subject entity's name is the relation subject.
            subj = store.get_entity(f["subject_id"])
            assert subj["name"] in {"用户", "FastAPI", "Logseq"}


def test_no_memoryitem_table_schema_holds(fresh_db):
    """ADR-2: there is no MemoryItem table — Fact reification is self-contained.
    The only base tables are entity + fact."""
    conn = db.get_conn()
    tables = {
        r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "entity" in tables and "fact" in tables
    assert "memoryitem" not in tables and "memory_item" not in tables
