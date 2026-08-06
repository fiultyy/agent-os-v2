"""mem-service consolidate — decay pass + dedup (ADR-8 + ADR-6 dedup).

Two phases per ``consolidate()`` call (Spec §2: decay then dedup):

1. **decay** (ADR-8): LIF *= 0.5**(Δt/half_life) where Δt is the age in days
   from ``created_at`` to now. half_life is per ``fact_type``:
   ephemeral=7d / stable=90d / permanent=∞ (no decay). Facts whose decayed
   LIF drops below 0.1 flip ``active → deprecated`` (v1 only had active +
   superseded; schema status already permits deprecated).
2. **dedup** (ADR-6): merge Facts sharing the same (subject_id, predicate,
   object_key); survivor absorbs max-LIF + union of source_refs, the rest
   flip to ``superseded`` pointing at it.

Decay is one-way (LIF only ever shrinks) but ``consolidate`` is idempotent —
re-running a fully-decayed Fact applies the next Δt slice; an already-
deprecated Fact stays deprecated.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import db
import store

# ADR-8 half-life table (days). permanent ⇒ ∞ ⇒ never decays.
HALF_LIFE_DAYS: dict[str, float] = {
    "ephemeral": 7.0,
    "stable": 90.0,
    "permanent": float("inf"),
}

# ADR-8: LIF below this threshold after decay ⇒ active → deprecated.
DEPRECATE_LIF_THRESHOLD = 0.1


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

    permanent Facts and Facts with unparseable ``created_at`` keep their LIF.
    """
    half_life = HALF_LIFE_DAYS.get(fact.get("fact_type") or "stable", 90.0)
    if half_life == float("inf"):
        return float(fact["LIF"]), False
    try:
        created = _parse_iso(fact["created_at"])
    except (ValueError, TypeError):
        return float(fact["LIF"]), False
    delta_days = max(0.0, (now - created).total_seconds() / 86400.0)
    new_lif = float(fact["LIF"]) * (0.5 ** (delta_days / half_life))
    return new_lif, new_lif < DEPRECATE_LIF_THRESHOLD


def _object_key(fact: dict[str, Any]) -> str:
    """Identity of the Fact's object side — binary entity→entity uses object_id,
    literal/unary uses value. None coerced to empty string for stable grouping."""
    return fact.get("object_id") or fact.get("value") or ""


def decay() -> dict[str, int]:
    """Run one LIF decay pass over the active Fact set (ADR-8).

    For each active Fact: LIF *= 0.5**(Δt/half_life), Δt = age in days from
    ``created_at`` to now. Facts whose LIF drops below 0.1 flip
    ``active → deprecated`` (schema status permits it; v1 had no writer).

    Idempotent: re-running applies the next Δt slice. Already-deprecated
    Facts are excluded so their LIF is frozen at the threshold-crossing pass.

    Returns ``{"decayed": <Facts whose LIF changed>, "deprecated": <Facts
    flipped active→deprecated this pass>}``.
    """
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
