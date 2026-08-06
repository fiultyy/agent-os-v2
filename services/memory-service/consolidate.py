"""mem-service consolidate — dedup skeleton (ADR-6 v1: dedup only, no decay).

Merges Facts sharing the same (subject_id, predicate, object) — where "object"
is object_id for binary entity→entity Facts, value for literal/unary Facts.
Keeps one representative per group, marks the rest ``superseded`` pointing at
it, folds their LIF + source_refs into the survivor.

type-aware decay is deferred (needs per-type half_life + trigger design; rides
the autoDream consolidate phase per ADR-6).
"""

from __future__ import annotations

import json
from typing import Any

import db
import store


def _object_key(fact: dict[str, Any]) -> str:
    """Identity of the Fact's object side — binary entity→entity uses object_id,
    literal/unary uses value. None coerced to empty string for stable grouping."""
    return fact.get("object_id") or fact.get("value") or ""


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
    """Run one dedup pass over the active Fact set.

    Returns ``{"groups": <duplicate groups collapsed>, "facts_merged": <total
    superseded Facts>}``. Idempotent: a clean run with no dups returns zeros.
    """
    conn = db.get_conn()  # ensures schema initialised on first call
    groups = _group_duplicate_facts(conn)
    total = 0
    for _, members in groups.items():
        total += _merge_group(members)
    return {"groups": len(groups), "facts_merged": total}
