"""mem-service cli — ingest / recall / consolidate (ADR-1, ADR-5, ADR-5b).

Top seam is the cli module (Spec §6): both ``cli.ingest(...)`` (Python) and
``python cli.py ingest "..."`` (argv) drive the same pipeline.

- ingest: extract facts via the adapter (ADR-5b butterfly-wing LLM with regex
  fallback), persist them. ``fact.extractor`` = "llm" when the adapter's LLM
  vote won, "regex" when it fell back (ADR-5 upheld as fallback).
- recall: KG navigation → Fact list + α·match+β·centrality+γ·LIF 加权排序
  (ADR-4v2). scoring.ALPHA_MATCH/BETA_CENTRALITY/GAMMA_LIF fuse token match,
  pagerank centrality and LIF into the recall order. v1 substring match on
  Fact.value/predicate + entity.name LIKE underlies the match term (semantic
  recall deferred — ADR-4, Spec Defer).
- consolidate: dedup skeleton, no decay (Spec §4 story 4; Node D owns depth).

No ``query`` subcommand (debug via ``recall --verbose`` or sqlite3 — Spec §3).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import adapter
import autodream as autodream_mod
import bootstrap
import consolidate as consolidate_mod
import recall as recall_mod
import store
from llm_provider import CCRProvider, LLMProvider


# ── ingest ──────────────────────────────────────────────────────────

def ingest(text: str, source_ref: str | None = None,
           fact_type: str = "stable",
           providers: list[LLMProvider] | None = None,
           source_cwd: str | None = None) -> dict[str, Any]:
    """Extract facts from ``text`` via the adapter and persist them to the KG.

    The adapter runs butterfly-wing LLM extraction (ADR-5b) and falls back to
    the regex extractor (ADR-5) when no provider is reachable or confidence is
    low. ``fact.extractor`` reflects which path won: "llm" or "regex".
    ``fact_type`` is ADR-8 (default stable; ingest ``--fact-type`` overrides).
    Entities are lazily created from each fact's subject/object via
    ``_ensure_entity`` (re-extraction of a known name reuses its id).

    ``providers`` defaults to ``[CCRProvider()]`` (the deployed ccr router).
    Pass ``[]`` to force the regex fallback path (Spec §4 story 5).

    Returns a summary ``{"entities": n, "facts": [...]}`` (fact ids).
    """
    if providers is None:
        providers = [CCRProvider()]
    extracted = adapter.extract_facts(text, providers=providers)
    # "llm" when the adapter's LLM vote produced the surviving facts (its
    # source_meta carries provider ≠ "regex"); "regex" on fallback.
    ext_label = "regex" if extracted.source_meta.get("provider") == "regex" else "llm"
    source_refs = [source_ref] if source_ref else []

    # name → entity_id cache (this ingest's working set).
    name_to_id: dict[str, str] = {}

    fact_ids: list[str] = []
    for fact in extracted.facts:
        subj_id = _ensure_entity(fact.subject, name_to_id)
        if subj_id is None:
            continue
        # Object may be a multi-word phrase; store as literal value AND try to
        # link an object entity if the object name was seen this ingest. ADR-3:
        # object is value-carrier; object_id optional. Prefer linking when known.
        obj_name = fact.object
        obj_id = name_to_id.get(obj_name)
        fid = store.put_fact(
            subject_id=subj_id,
            predicate=fact.predicate,
            value=obj_name,
            object_id=obj_id,
            extractor=ext_label,
            fact_type=fact_type,
            source_cwd=source_cwd,
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

def recall(query: str, verbose: bool = False,
           session_id: str | None = None, boost: bool = True,
           weights=None, use_vec: bool = False, delta: float | None = None,
           cwd: str | None = None) -> list[dict[str, Any]]:
    """Return Facts relevant to ``query``, ordered by α·match+β·centrality+γ·LIF(+δ·vec_sim use_vec) 加权排序 (ADR-4v2/ADR-13).

    Thin wrapper over ``recall.recall``. ``use_vec=True`` 启用向量召回融合
    (ADR-13); ``cwd`` ADR-14 b 方案: 过滤 source_cwd(含 NULL 老数据兼容)。
    """
    return recall_mod.recall(query, verbose=verbose, session_id=session_id,
                             boost=boost, weights=weights, use_vec=use_vec, delta=delta, cwd=cwd)


# ── consolidate ────────────────────────────────────────────────────

def consolidate() -> dict[str, int]:
    """Decay + dedup pass (Spec §4.4; ADR-8 + ADR-6).

    Thin wrapper over ``consolidate.consolidate``. Phase 1 decays LIF per
    fact_type half-life (active→deprecated when LIF<0.1); phase 2 marks
    exact-duplicate Facts as superseded. Returns ``{decayed, deprecated,
    superseded, active}`` per the SKILL.md output contract.
    """
    return consolidate_mod.consolidate()


# ── autodream ──────────────────────────────────────────────────────

def autodream(session_id: str, transcript_path: str, use_regex: bool = False,
              cwd: str | None = None) -> dict[str, int]:
    """PreCompact autoDream: session transcript raw→KG incremental (ADR-10/11).

    Thin wrapper over ``autodream.autodream``. ``cwd`` ADR-14 b 方案: 记 source_cwd
    (fact 来源 cwd, recall --cwd 过滤)。Driven by ``cli autodream`` from PreCompact hook。
    """
    providers = [] if use_regex else None  # None → default_providers (LLM 蝴蝶翼)
    return autodream_mod.autodream(session_id, transcript_path, providers=providers, source_cwd=cwd)


# ── init-memory (bootstrap) ─────────────────────────────────────────

def init_memory(memory_dir: str | None = None, use_regex: bool = False,
                source_cwd: str | None = None) -> dict[str, int]:
    """Seed KG from CC memory .md files (ADR-12). ``source_cwd`` ADR-14 记来源 cwd。
    Thin wrapper over ``bootstrap.init_memory``."""
    if memory_dir is None:
        memory_dir = str(Path.home() / ".claude" / "projects" / "-home-yy--claude" / "memory")
    providers = [] if use_regex else None
    return bootstrap.init_memory(memory_dir, providers=providers, source_cwd=source_cwd)


# ── build-index (投影 → CC memory, ADR-15 分布式 index) ─────────────

def build_index(scope: str | None = None, top_k: int = 20, memory_dir: str | None = None) -> dict:
    """投影 KG 高 LIF top-K fact → CC memory/mem-<id>.md + MEMORY.md [mem] 索引行(真嵌入 CC)。
    PreCompact(autodream 后硬编)/ new / cli 触发。"""
    import db
    import projection
    import store
    cwd = scope or os.getcwd()
    mem_dir = Path(memory_dir) if memory_dir else projection.cc_memory_dir(cwd)
    conn = db.get_conn()
    rows = conn.execute(
        "SELECT * FROM fact WHERE status='active' AND (source_cwd=? OR source_cwd IS NULL) "
        "ORDER BY LIF DESC LIMIT ?",
        (cwd, top_k)).fetchall()
    facts = [store._decode_fact(r) for r in rows]
    ent_names = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM entity").fetchall()}
    return projection.build_index(facts, ent_names, mem_dir)


# ── argv entry ──────────────────────────────────────────────────────

def _main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="cli", description="mem-service cli")
    sub = p.add_subparsers(dest="cmd", required=True)

    ing = sub.add_parser("ingest", help="extract+store text")
    ing.add_argument("text")
    ing.add_argument("--source", default=None)
    ing.add_argument(
        "--fact-type",
        dest="fact_type",
        default="stable",
        choices=("ephemeral", "stable", "permanent"),
        help="Fact lifetime class for decay (ADR-8); default stable",
    )

    rec = sub.add_parser("recall", help="recall facts for query")
    rec.add_argument("query")
    rec.add_argument("--verbose", action="store_true")
    rec.add_argument("--vector", action="store_true",
                     help="启用向量召回融合(ADR-13, 解 synonym/rewrite 字面盲区)")
    rec.add_argument("--cwd", dest="cwd", default=None,
                     help="ADR-14 过滤 source_cwd(本 cwd fact + NULL 老数据; 默认全 cwd)")

    sub.add_parser("consolidate", help="dedup skeleton")

    dream = sub.add_parser("autodream", help="session transcript raw→KG incremental (ADR-10/11)")
    dream.add_argument("--session", dest="session", required=True, help="CC session id")
    dream.add_argument(
        "--transcript", dest="transcript", required=True,
        help="path to CC transcript JSONL",
    )
    dream.add_argument(
        "--regex", action="store_true",
        help="强制 regex 抽取(调试/fallback, 默认 LLM 蝴蝶翼 ADR-5b)",
    )
    dream.add_argument(
        "--cwd", dest="cwd", default=None,
        help="ADR-14 记 source_cwd(来源 cwd, 从 hook stdin cwd 传)",
    )

    initmem = sub.add_parser("init-memory", help="seed KG from CC memory .md (ADR-12)")
    initmem.add_argument(
        "--memory-dir", dest="memory_dir", default=None,
        help="CC memory dir (默认 ~/.claude/projects/-home-yy--claude/memory/)",
    )
    initmem.add_argument(
        "--regex", action="store_true",
        help="强制 regex 抽取(调试/fallback, 默认 LLM 蝴蝶翼)",
    )
    initmem.add_argument(
        "--cwd", dest="cwd", default=None,
        help="ADR-14 记 source_cwd(来源 cwd, 默认 NULL)",
    )

    bi = sub.add_parser("build-index",
                        help="投影 KG 高 LIF fact → CC memory + MEMORY.md (ADR-15 分布式 index)")
    bi.add_argument("--scope", default=None, help="来源 cwd(默认 os.getcwd)")
    bi.add_argument("--top-k", dest="top_k", type=int, default=20)
    bi.add_argument("--memory-dir", dest="memory_dir", default=None,
                    help="CC memory dir(默认 ~/.claude/projects/<encoded>/memory/)")

    args = p.parse_args(argv)
    if args.cmd == "ingest":
        print(json.dumps(
            ingest(args.text, source_ref=args.source, fact_type=args.fact_type,
                   source_cwd=os.getcwd()),
            ensure_ascii=False,
        ))
    elif args.cmd == "recall":
        print(json.dumps(recall(args.query, verbose=args.verbose, use_vec=args.vector, cwd=args.cwd), ensure_ascii=False, default=str))
    elif args.cmd == "consolidate":
        print(json.dumps(consolidate()))
    elif args.cmd == "autodream":
        print(json.dumps(autodream(args.session, args.transcript, use_regex=args.regex, cwd=args.cwd), ensure_ascii=False))
    elif args.cmd == "init-memory":
        print(json.dumps(init_memory(args.memory_dir, use_regex=args.regex, source_cwd=args.cwd), ensure_ascii=False))
    elif args.cmd == "build-index":
        print(json.dumps(build_index(scope=args.scope, top_k=args.top_k, memory_dir=args.memory_dir), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(_main())
