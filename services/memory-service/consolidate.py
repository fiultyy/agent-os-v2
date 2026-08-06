"""mem-service consolidate — decay pass + dedup (ADR-8 + ADR-6 dedup).

Two phases per ``consolidate()`` call (Spec §2: decay then dedup):

1. **decay** (ADR-8, idempotent): ``new_lif = original_lif *
   0.5**(Δt/half_life)`` where Δt is the age in days from ``created_at``
   (immutable) to now, and ``original_lif`` is frozen at store time. Because
   the rebasing starts from ``original_lif`` (not the already-decayed ``LIF``)
   and ``created_at`` never moves, re-running consolidate recomputes the same
   ``new_lif`` for the same wall clock — no compounding across passes. half_life
   is per ``fact_type``: ephemeral=7d / stable=90d / permanent=∞ (no decay).
   Facts whose decayed LIF drops below 0.1 flip ``active → deprecated`` (v1
   only had active + superseded; schema status already permits deprecated).
2. **dedup** (ADR-6): merge Facts sharing the same (subject_id, predicate,
   object_key); survivor absorbs max-LIF + union of source_refs, the rest
   flip to ``superseded`` pointing at it.

``original_lif`` (ADR-8 idempotency column) lives on the fact table; legacy
DBs without it are backfilled to ``LIF`` by :func:`_ensure_schema` (one-time,
idempotent ALTER).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import db
import scoring
import store

# ADR-8 half-life table (days). permanent ⇒ ∞ ⇒ never decays.
HALF_LIFE_DAYS: dict[str, float] = {
    "ephemeral": 7.0,
    "stable": 90.0,
    "permanent": float("inf"),
}

# ADR-8: LIF below this threshold after decay ⇒ active → deprecated.
DEPRECATE_LIF_THRESHOLD = 0.1

# ADR-8v2 source-dim weight by extractor. Canonical home is scoring.py (the
# LIF-Scorer node); re-exported here so store.put_fact and _ensure_schema's
# legacy backfill keep a single source of truth.
SOURCE_WEIGHT: dict[str, float] = scoring.SOURCE_WEIGHT

_schema_migrated = False


def _ensure_schema() -> None:
    """Idempotent schema migration: back-fill ADR-8 ``original_lif`` and the
    ADR-8v2 LIF five-dim columns for legacy fact tables.

    schema.sql adds the columns on fresh DBs, but ``CREATE TABLE IF NOT
    EXISTS`` skips existing tables — so pre-ADR-8/8v2 DBs need ALTERs here.

    Backfill (ADR-8v2): ``lif_source = SOURCE_WEIGHT[extractor]`` (regex=0.4
    default for unknown extractors), ``lif_recency = 0.5`` (mid-neutral, the
    decay pass recomputes from last_accessed_at=created_at on first run); all
    other new dims default 0 and ``original_lif`` semantics shifts from decay
    base to the source-dim initial-value snapshot.
    """
    global _schema_migrated
    if _schema_migrated:
        return
    conn = db.get_conn()
    cols = {r[1] for r in conn.execute("PRAGMA table_info(fact)").fetchall()}

    # ADR-8 idempotency column (legacy backfill only).
    if "original_lif" not in cols:
        conn.execute("ALTER TABLE fact ADD COLUMN original_lif REAL NOT NULL DEFAULT 0.5")
        conn.execute("UPDATE fact SET original_lif = LIF")

    # ADR-8v2 LIF five-dim + recall-reinforcement state.
    if "lif_freq" not in cols:
        conn.execute("ALTER TABLE fact ADD COLUMN lif_freq REAL NOT NULL DEFAULT 0")
    if "lif_recency" not in cols:
        conn.execute("ALTER TABLE fact ADD COLUMN lif_recency REAL NOT NULL DEFAULT 0.5")
    if "lif_spread" not in cols:
        conn.execute("ALTER TABLE fact ADD COLUMN lif_spread REAL NOT NULL DEFAULT 0")
    if "lif_coherence" not in cols:
        conn.execute("ALTER TABLE fact ADD COLUMN lif_coherence REAL NOT NULL DEFAULT 0")
    if "lif_source" not in cols:
        # Backfill source-dim from extractor (regex=0.4 fallback for unknown).
        conn.execute("ALTER TABLE fact ADD COLUMN lif_source REAL NOT NULL DEFAULT 0.4")
        for ext, w in SOURCE_WEIGHT.items():
            conn.execute("UPDATE fact SET lif_source = ? WHERE extractor = ?", (w, ext))
    if "access_count" not in cols:
        conn.execute("ALTER TABLE fact ADD COLUMN access_count INTEGER NOT NULL DEFAULT 0")
    if "last_accessed_at" not in cols:
        # NULL ⇒ first decay/recency pass treats created_at as last access.
        conn.execute("ALTER TABLE fact ADD COLUMN last_accessed_at TEXT")
    if "seen_sessions" not in cols:
        conn.execute("ALTER TABLE fact ADD COLUMN seen_sessions TEXT NOT NULL DEFAULT '[]'")

    conn.commit()
    _schema_migrated = True


def _parse_iso(ts: str) -> datetime:
    """Parse an ISO-8601 timestamp (``created_at``) to an aware datetime.

    ``datetime.fromisoformat`` handles the ``+00:00`` suffix we write in
    store._now; naive timestamps are stamped UTC to bound Δt ≥ 0.
    """
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _decay_one(fact: dict[str, Any], now: datetime) -> tuple[float, bool]:
    """Return (new LIF, deprecate?) for a Fact per ADR-8.

    Idempotent rebasing: ``new_lif = original_lif * 0.5**(Δt/half_life)``,
    Δt = ``now - created_at`` (created_at immutable). original_lif is frozen at
    store time, so repeated consolidate calls with the same wall clock produce
    the same new_lif — decay never compounds across passes. permanent Facts and
    Facts with unparseable ``created_at`` keep their LIF.
    """
    half_life = HALF_LIFE_DAYS.get(fact.get("fact_type") or "stable", 90.0)
    if half_life == float("inf"):
        return float(fact["LIF"]), False
    try:
        created = _parse_iso(fact["created_at"])
    except (ValueError, TypeError):
        return float(fact["LIF"]), False
    delta_days = max(0.0, (now - created).total_seconds() / 86400.0)
    base = float(fact.get("original_lif", fact["LIF"]))
    new_lif = base * (0.5 ** (delta_days / half_life))
    return new_lif, new_lif < DEPRECATE_LIF_THRESHOLD


def _object_key(fact: dict[str, Any]) -> str:
    """Identity of the Fact's object side — binary entity→entity uses object_id,
    literal/unary uses value. None coerced to empty string for stable grouping."""
    return fact.get("object_id") or fact.get("value") or ""


def decay() -> dict[str, int]:
    """Run one LIF decay pass over the active Fact set (ADR-8, idempotent).

    For each active Fact: ``new_lif = original_lif * 0.5**(Δt/half_life)``,
    Δt = age in days from ``created_at`` to now; ``original_lif`` is frozen at
    store time and ``created_at`` is immutable, so the same wall clock yields
    the same new_lif on every call (no compounding). Facts whose LIF drops
    below 0.1 flip ``active → deprecated`` (schema status permits it; v1 had
    no writer).

    Idempotent: re-running with no wall-clock progress produces no LIF change.
    Already-deprecated Facts are excluded so their LIF is frozen at the
    threshold-crossing pass.

    Returns ``{"decayed": <Facts whose LIF changed>, "deprecated": <Facts
    flipped active→deprecated this pass>}``.
    """
    _ensure_schema()
    conn = db.get_conn()
    now = datetime.now(timezone.utc)
    rows = conn.execute(
        "SELECT * FROM fact WHERE status = 'active'"
    ).fetchall()
    facts = [store._decode_fact(r) for r in rows]

    decayed = 0
    deprecated = 0
    for f in facts:
        new_lif, deprecate = _decay_one(f, now)
        if new_lif == float(f["LIF"]) and not deprecate:
            continue  # permanent or no time elapsed — no write
        conn.execute(
            "UPDATE fact SET LIF = ? WHERE id = ?", (new_lif, f["id"])
        )
        decayed += 1
        if deprecate:
            store.update_fact_status(f["id"], "deprecated")
            deprecated += 1
    if decayed:
        conn.commit()
    return {"decayed": decayed, "deprecated": deprecated}


def _group_duplicate_facts(conn: Any) -> dict[tuple[str, str, str], list[dict[str, Any]]]:
    """Group ACTIVE facts by (subject_id, predicate, object_key); return only
    groups with more than one member (the actual duplicates)."""
    rows = conn.execute(
        "SELECT * FROM fact WHERE status = 'active' ORDER BY created_at ASC"
    ).fetchall()
    facts = [store._decode_fact(r) for r in rows]
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for f in facts:
        key = (f["subject_id"], f["predicate"], _object_key(f))
        groups.setdefault(key, []).append(f)
    return {k: v for k, v in groups.items() if len(v) > 1}


def _merge_group(group: list[dict[str, Any]]) -> int:
    """Collapse one duplicate group to its survivor (first/oldest by created_at).
    Survivor absorbs max-LIF and the union of source_refs from the rest; the rest
    flip to status='superseded' pointing at the survivor. Returns count merged."""
    survivor = group[0]
    merged = 0
    new_lif = float(survivor["LIF"])
    new_refs: list[str] = list(survivor["source_refs"])
    survivor_id = survivor["id"]

    for dup in group[1:]:
        new_lif = max(new_lif, float(dup["LIF"]))
        for ref in dup["source_refs"]:
            if ref not in new_refs:
                new_refs.append(ref)
        # ponytail: no transaction — single-writer cli, crash leaves at worst a
        # half-merged group re-runnable on next consolidate (idempotent: already
        # superseded dups fall out of the active group next pass).
        store.update_fact_status(dup["id"], "superseded", supersedes_id=survivor_id)
        merged += 1

    conn = db.get_conn()
    conn.execute(
        "UPDATE fact SET LIF = ?, source_refs = ? WHERE id = ?",
        (new_lif, json.dumps(new_refs, ensure_ascii=False), survivor_id),
    )
    conn.commit()
    return merged


def consolidate() -> dict[str, int]:
    """Run decay + dedup over the Fact set (Spec §2: decay then dedup).

    Phase 1 decay (ADR-8): LIF *= 0.5**(Δt/half_life); LIF<0.1 flips
    active→deprecated. Phase 2 dedup (ADR-6): exact-duplicate Facts collapse
    to a survivor, the rest flip active→superseded.

    Returns ``{"decayed": ..., "deprecated": ..., "superseded": ...,
    "active": <unique Facts remaining active>}``. Idempotent: a clean run
    with no decay/dups returns zeros.
    """
    conn = db.get_conn()  # ensures schema initialised on first call
    decay_out = decay()
    groups = _group_duplicate_facts(conn)
    superseded = 0
    for _, members in groups.items():
        superseded += _merge_group(members)
    # Active Facts remaining post-merge (dups already flipped to 'superseded').
    active = conn.execute(
        "SELECT COUNT(*) FROM fact WHERE status = 'active'"
    ).fetchone()[0]
    return {
        "decayed": decay_out["decayed"],
        "deprecated": decay_out["deprecated"],
        "superseded": superseded,
        "active": active,
    }
