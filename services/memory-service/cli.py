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

import consolidate as consolidate_mod
import extractor
import recall as recall_mod
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

    Thin wrapper over ``recall.recall`` (Node C depth: token-split match_item
    × LIF). Spec §6 seam — the cli subcommand and ``cli.recall(...)`` drive
    the same pipeline as the deepened module.
    """
    return recall_mod.recall(query, verbose=verbose)


# ── consolidate ────────────────────────────────────────────────────

def consolidate() -> dict[str, int]:
    """Dedup pass — mark exact-duplicate facts as superseded (Spec §4.4).

    Thin wrapper over ``consolidate.consolidate`` (Node D depth: survivor
    absorbs max-LIF + union of source_refs). Returns ``{superseded, active}``
    per the SKILL.md output contract. No decay (ADR-6).
    """
    return consolidate_mod.consolidate()


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
