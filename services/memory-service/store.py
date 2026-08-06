"""mem-service store — Entity + Fact CRUD over SQLite (ADR-2, ADR-3).

No MemoryItem layer — Fact reification is self-contained. Entity↔Fact linkage
is via Fact.subject_id/object_id (reverse lookup); raw provenance lives on
Fact.source_refs.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

import db


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uid() -> str:
    return uuid.uuid4().hex


# ── Entity ──────────────────────────────────────────────────────────

def put_entity(name: str, entity_type: str, properties: dict[str, Any] | None = None,
               entity_id: str | None = None) -> str:
    """Insert an entity, return its id. Caller dedups upstream if desired."""
    conn = db.get_conn()
    eid = entity_id or _uid()
    conn.execute(
        "INSERT INTO entity (id, name, entity_type, properties, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (eid, name, entity_type, json.dumps(properties or {}, ensure_ascii=False), _now()),
    )
    conn.commit()
    return eid


def get_entity(entity_id: str) -> dict[str, Any] | None:
    conn = db.get_conn()
    row = conn.execute("SELECT * FROM entity WHERE id = ?", (entity_id,)).fetchone()
    if row is None:
        return None
    return _decode_entity(row)


def find_entities_by_name(name: str, entity_type: str | None = None) -> list[dict[str, Any]]:
    """Exact-name lookup (dedup helper; v1 recall uses LIKE, not this)."""
    conn = db.get_conn()
    if entity_type:
        rows = conn.execute(
            "SELECT * FROM entity WHERE name = ? AND entity_type = ?", (name, entity_type)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM entity WHERE name = ?", (name,)).fetchall()
    return [_decode_entity(r) for r in rows]


def _decode_entity(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "entity_type": row["entity_type"],
        "properties": json.loads(row["properties"]) if row["properties"] else {},
        "created_at": row["created_at"],
    }


# ── Fact ────────────────────────────────────────────────────────────

def put_fact(
    subject_id: str,
    predicate: str,
    value: str | None = None,
    *,
    object_id: str | None = None,
    fact_type: str = "stable",
    LIF: float = 0.5,
    confidence: float = 0.5,
    source_refs: list[str] | None = None,
    extractor: str = "regex",
    valid_from: str | None = None,
    valid_to: str | None = None,
    status: str = "active",
    supersedes_id: str | None = None,
    fact_id: str | None = None,
) -> str:
    """Insert a Fact (reified), return its id.

    Literal/unary facts: pass ``value`` only (object_id stays None).
    Binary entity→entity facts: pass ``object_id`` (value optional).
    """
    conn = db.get_conn()
    fid = fact_id or _uid()
    conn.execute(
        """INSERT INTO fact
           (id, subject_id, predicate, object_id, value, valid_from, valid_to,
            fact_type, LIF, confidence, source_refs, extractor, status,
            supersedes_id, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            fid, subject_id, predicate, object_id, value, valid_from, valid_to,
            fact_type, LIF, confidence,
            json.dumps(source_refs or [], ensure_ascii=False),
            extractor, status, supersedes_id, _now(),
        ),
    )
    conn.commit()
    return fid


def get_fact(fact_id: str) -> dict[str, Any] | None:
    conn = db.get_conn()
    row = conn.execute("SELECT * FROM fact WHERE id = ?", (fact_id,)).fetchone()
    return _decode_fact(row) if row else None


def get_facts_by_subject(subject_id: str, status: str | None = "active") -> list[dict[str, Any]]:
    conn = db.get_conn()
    if status:
        rows = conn.execute(
            "SELECT * FROM fact WHERE subject_id = ? AND status = ? ORDER BY created_at DESC",
            (subject_id, status),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM fact WHERE subject_id = ? ORDER BY created_at DESC", (subject_id,)
        ).fetchall()
    return [_decode_fact(r) for r in rows]


def update_fact_status(fact_id: str, status: str, supersedes_id: str | None = None) -> None:
    """Lifecycle transition (active→deprecated/superseded). No-op if missing."""
    conn = db.get_conn()
    conn.execute(
        "UPDATE fact SET status = ?, supersedes_id = COALESCE(?, supersedes_id) WHERE id = ?",
        (status, supersedes_id, fact_id),
    )
    conn.commit()


def _decode_fact(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "subject_id": row["subject_id"],
        "predicate": row["predicate"],
        "object_id": row["object_id"],
        "value": row["value"],
        "valid_from": row["valid_from"],
        "valid_to": row["valid_to"],
        "fact_type": row["fact_type"],
        "LIF": row["LIF"],
        "confidence": row["confidence"],
        "source_refs": json.loads(row["source_refs"]) if row["source_refs"] else [],
        "extractor": row["extractor"],
        "status": row["status"],
        "supersedes_id": row["supersedes_id"],
        "created_at": row["created_at"],
    }
