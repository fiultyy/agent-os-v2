"""ADR-1 (Node D): observe-side agent_id end-to-end.

ObserveEvent carries agent_id; observe_sessions table gets an agent_id column
via idempotent migration (legacy rows NULL-safe); session create + backfill
work; round-trip serialization preserves agent_id.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from src.events import ObserveEvent, EventType
from src.session_store import SessionStore


def test_observe_event_has_agent_id_default_empty() -> None:
    ev = ObserveEvent(harness_type="agent-os-v2")
    assert ev.agent_id == ""  # default; legacy-compatible
    d = ev.to_dict()
    assert d["agent_id"] == ""
    # round-trip
    rt = ObserveEvent.from_dict(d)
    assert rt.agent_id == ""


def test_observe_event_carries_agent_id_roundtrip() -> None:
    ev = ObserveEvent(
        harness_type="agent-os-v2", session_id="s1", agent_id="main",
        event_type=EventType.TICK_STARTED,
    )
    d = ev.to_dict()
    assert d["agent_id"] == "main"
    rt = ObserveEvent.from_dict(d)
    assert rt.agent_id == "main"


def test_from_dict_tolerates_missing_agent_id_legacy() -> None:
    # An old event dict without agent_id must still deserialize.
    legacy = {
        "event_id": "x", "harness_type": "agent-os-v2", "harness_id": "h",
        "session_id": "s", "tick_id": "t",
        "event_type": "tick_started", "data": {},
        "timestamp": "2026-07-22T00:00:00+00:00",
    }
    ev = ObserveEvent.from_dict(legacy)
    assert ev.agent_id == ""


def test_session_table_has_agent_id_column_and_legacy_null_safe(tmp_path: Path) -> None:
    """Migration adds agent_id; pre-existing rows keep NULL; reads still work."""
    db = tmp_path / "obs.db"
    # Pre-create the OLD schema (no agent_id) and insert a legacy session row.
    import sqlite3
    conn = sqlite3.connect(str(db))
    conn.executescript(
        """
        CREATE TABLE observe_sessions (
            harness_type TEXT NOT NULL, session_id TEXT NOT NULL,
            harness_id TEXT NOT NULL, created_at TEXT NOT NULL,
            last_active TEXT NOT NULL,
            PRIMARY KEY (harness_type, session_id)
        );
        INSERT INTO observe_sessions VALUES ('claw','legacy-s','h','t','t');
        """
    )
    conn.commit()
    conn.close()

    # Now open via SessionStore — it must run the ALTER migration idempotently.
    store = SessionStore(db_path=db)
    # Legacy row readable, agent_id NULL.
    sess = store.get_session("claw", "legacy-s")
    assert sess is not None
    assert sess["agent_id"] is None  # legacy NULL preserved

    # New session with agent_id.
    store.create_session("agent-os-v2", "s1", "h1", agent_id="main")
    sess = store.get_session("agent-os-v2", "s1")
    assert sess["agent_id"] == "main"

    # Re-init idempotency: second construction must not raise.
    store2 = SessionStore(db_path=db)
    assert store2.get_session("agent-os-v2", "s1")["agent_id"] == "main"
    store.close()
    store2.close()


def test_update_agent_id_backfills_only_when_null(tmp_path: Path) -> None:
    db = tmp_path / "obs.db"
    store = SessionStore(db_path=db)
    store.create_session("agent-os-v2", "s1", "h1")  # no agent_id → NULL
    store.update_agent_id("agent-os-v2", "s1", "main")
    assert store.get_session("agent-os-v2", "s1")["agent_id"] == "main"
    # Non-destructive: explicit agent_id at create is NOT overwritten by backfill.
    store.create_session("agent-os-v2", "s2", "h2", agent_id="native")
    store.update_agent_id("agent-os-v2", "s2", "main")
    assert store.get_session("agent-os-v2", "s2")["agent_id"] == "native"
    store.close()


def test_event_store_persists_agent_id_roundtrip(tmp_path: Path) -> None:
    """ADR-1: observe_events table stores + reads back agent_id (Node D MUST)."""
    import asyncio
    from src.event_store import EventStore

    db = tmp_path / "obs.db"
    es = EventStore(db_path=db)
    ev = ObserveEvent(
        harness_type="agent-os-v2", session_id="s1", tick_id="t1",
        agent_id="main", event_type=EventType.TICK_STARTED, data={"request": "hi"},
    )
    asyncio.run(es.append(ev))
    rows = es.get_events("agent-os-v2", "s1")
    assert len(rows) == 1
    assert rows[0]["agent_id"] == "main"
    es.close()


def test_event_store_legacy_null_agent_id(tmp_path: Path) -> None:
    """Legacy event rows (NULL agent_id) survive migration and are readable."""
    import asyncio
    import sqlite3
    from src.event_store import EventStore

    db = tmp_path / "obs.db"
    # Old schema, no agent_id column, one legacy row.
    conn = sqlite3.connect(str(db))
    conn.executescript(
        """
        CREATE TABLE observe_events (
            event_id TEXT PRIMARY KEY, harness_type TEXT NOT NULL,
            harness_id TEXT NOT NULL, session_id TEXT NOT NULL,
            tick_id TEXT NOT NULL DEFAULT '', event_type TEXT NOT NULL,
            data TEXT NOT NULL DEFAULT '{}', timestamp TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        INSERT INTO observe_events VALUES
            ('e1','agent-os-v2','h','s1','t1','tick_started','{}','ts','ts');
        """
    )
    conn.commit()
    conn.close()
    # Migration on open.
    es = EventStore(db_path=db)
    rows = es.get_events("agent-os-v2", "s1")
    assert len(rows) == 1
    # Legacy row: agent_id NULL (column existed, row pre-dates column → NULL).
    assert rows[0]["agent_id"] is None
    es.close()
