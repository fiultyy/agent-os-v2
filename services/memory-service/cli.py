"""mem-service cli — ingest / recall / consolidate (ADR-1, ADR-5).

Top seam is the cli module (Spec §6): both ``cli.ingest(...)`` (Python) and
``python cli.py ingest "..."`` (argv) drive the same pipeline.

- ingest: regex-extract entities+facts, store them (fact.extractor="regex").
- recall: KG navigation → Fact list + match×lif ordering. v1 substring match
  on Fact.value/predicate + entity.name LIKE (semantic recall deferred — ADR-4,
  Spec Defer). Node C fleshes out scoring detail; here is the working skeleton
  so the closed loop (Node F) can run.
- consolidate: dedup skeleton, no decay (Spec §4 story 4; Node D owns depth).

No ``query`` subcommand (debug via ``recall --verbose`` or sqlite3 — Spec §3).
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

import extractor
import store


# ── ingest ──────────────────────────────────────────────────────────

def ingest(text: str, source_ref: str | None = None) -> dict[str, Any]:
    """Extract entities+facts from ``text`` and persist them to the KG.

    Each fact is stamped ``extractor="regex"`` (ADR-5). Entities dedup by
    (name, entity_type) — re-extraction of a known name reuses the existing
    entity id rather than creating a duplicate.

    Returns a summary ``{"entities": n, "facts": [...]}`` (fact ids).
    """
    extracted = extractor.extract(text)
    source_refs = [source_ref] if source_ref else []

    # name → entity_id cache (this ingest's working set).
    name_to_id: dict[str, str] = {}
    # Existing entities first so we dedup across ingests.
    for ent in extracted["entities"]:
        existing = store.find_entities_by_name(ent["name"], ent["entity_type"])
        if existing:
            name_to_id[ent["name"]] = existing[0]["id"]
    for ent in extracted["entities"]:
        if ent["name"] in name_to_id:
            continue
        name_to_id[ent["name"]] = store.put_entity(
            ent["name"], ent["entity_type"]
        )

    fact_ids: list[str] = []
    for fact in extracted["facts"]:
        subj_id = _ensure_entity(fact["subject"], name_to_id)
        if subj_id is None:
            continue
        # Object may be a multi-word phrase; store as literal value AND try to
        # link an object entity if the object name was extracted. ADR-3: object
        # is value-carrier; object_id optional. Prefer linking when known.
        obj_name = fact["object"]
        obj_id = name_to_id.get(obj_name)
        fid = store.put_fact(
            subject_id=subj_id,
            predicate=fact["predicate"],
            value=obj_name,
            object_id=obj_id,
            extractor="regex",
            source_refs=source_refs,
        )
        fact_ids.append(fid)

    return {"entities": len(name_to_id), "facts": fact_ids}


def _ensure_entity(name: str, cache: dict[str, str]) -> str | None:
    """Resolve a fact subject/object to an entity id, creating it if needed.

    Subjects/objects from relation patterns may be phrases not caught by the
    entity patterns (e.g. "用户", "笔记工具"); we still persist them as entities
    so the KG is navigable. None only on empty.
    """
    if not name:
        return None
    if name in cache:
        return cache[name]
    existing = store.find_entities_by_name(name)
    if existing:
        eid = existing[0]["id"]
    else:
        eid = store.put_entity(name, "inferred")
    cache[name] = eid
    return eid


# ── recall ──────────────────────────────────────────────────────────

def recall(query: str, verbose: bool = False) -> list[dict[str, Any]]:
    """Return Facts relevant to ``query``, ordered by match×lif (ADR-4).

    v1 navigation = substring/prefix match: entity.name LIKE query locates
    seed entities, then their facts (as subject OR object) are scored. Semantic
    recall / synonym rewrite deferred (Spec Defer; ADR-4).

    Node C owns the match_item detail; this is the working closed-loop path
    so ingest→recall (Node F) is testable now.
    """
    import db

    conn = db.get_conn()
    # Seed entity ids whose name contains the query (LIKE, case-insensitive).
    seed_rows = conn.execute(
        "SELECT id FROM entity WHERE name LIKE ?", (f"%{query}%",)
    ).fetchall()
    seed_ids = {r["id"] for r in seed_rows}

    facts: list[dict[str, Any]] = []
    if seed_ids:
        placeholders = ",".join("?" for _ in seed_ids)
        rows = conn.execute(
            f"SELECT * FROM fact WHERE status='active' "
            f"AND (subject_id IN ({placeholders}) OR object_id IN ({placeholders}))",
            (*seed_ids, *seed_ids),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM fact WHERE status='active' AND "
            "(value LIKE ? OR predicate LIKE ?)",
            (f"%{query}%", f"%{query}%"),
        ).fetchall()

    for row in rows:
        fact = store._decode_fact(row)
        # match×lif: literal hit weight 1.0 × LIF scalar (ADR-4).
        scored = round(1.0 * fact["LIF"], 4)
        if verbose:
            subj = store.get_entity(fact["subject_id"])
            obj = store.get_entity(fact["object_id"]) if fact["object_id"] else None
            fact["_scored"] = scored
            fact["_subject_name"] = subj["name"] if subj else None
            fact["_object_name"] = obj["name"] if obj else None
        facts.append(fact)

    facts.sort(key=lambda f: f.get("_scored", f["LIF"]), reverse=True)
    return facts


# ── consolidate ────────────────────────────────────────────────────

def consolidate() -> dict[str, int]:
    """Dedup skeleton — mark exact-duplicate facts as superseded (Spec §4.4).

    No decay (type-aware LIF decay deferred, ADR-6). Node D owns depth; this
    keeps the seam importable and the cli subcommand present.
    """
    import db

    conn = db.get_conn()
    rows = conn.execute(
        "SELECT id, subject_id, predicate, object_id, value FROM fact "
        "WHERE status='active' ORDER BY created_at"
    ).fetchall()
    seen: dict[tuple, str] = {}
    superseded = 0
    for r in rows:
        key = (r["subject_id"], r["predicate"], r["object_id"], r["value"])
        if key in seen:
            store.update_fact_status(r["id"], "superseded", supersedes_id=seen[key])
            superseded += 1
        else:
            seen[key] = r["id"]
    return {"superseded": superseded, "active": len(seen)}


# ── argv entry ──────────────────────────────────────────────────────

def _main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="cli", description="mem-service cli")
    sub = p.add_subparsers(dest="cmd", required=True)

    ing = sub.add_parser("ingest", help="extract+store text")
    ing.add_argument("text")
    ing.add_argument("--source", default=None)

    rec = sub.add_parser("recall", help="recall facts for query")
    rec.add_argument("query")
    rec.add_argument("--verbose", action="store_true")

    sub.add_parser("consolidate", help="dedup skeleton")

    args = p.parse_args(argv)
    if args.cmd == "ingest":
        print(json.dumps(ingest(args.text, source_ref=args.source), ensure_ascii=False))
    elif args.cmd == "recall":
        print(json.dumps(recall(args.query, verbose=args.verbose), ensure_ascii=False, default=str))
    elif args.cmd == "consolidate":
        print(json.dumps(consolidate()))
    return 0


if __name__ == "__main__":
    sys.exit(_main())
