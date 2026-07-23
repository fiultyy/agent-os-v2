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


# ── F3 (ADR-S5): observe parent_session_id (fork lineage) ─────────────


def test_observe_event_has_parent_session_id_default_empty() -> None:
    ev = ObserveEvent(harness_type="agent-os-v2")
    assert ev.parent_session_id == ""  # default; legacy-compatible
    d = ev.to_dict()
    assert d["parent_session_id"] == ""
    rt = ObserveEvent.from_dict(d)
    assert rt.parent_session_id == ""


def test_observe_event_parent_session_id_roundtrip() -> None:
    ev = ObserveEvent(
        harness_type="agent-os-v2", session_id="child",
        parent_session_id="parent-src", agent_id="main",
        event_type=EventType.BRANCH_CREATED,
    )
    d = ev.to_dict()
    assert d["parent_session_id"] == "parent-src"
    rt = ObserveEvent.from_dict(d)
    assert rt.parent_session_id == "parent-src"


def test_from_dict_tolerates_missing_parent_session_id_legacy() -> None:
    legacy = {
        "event_id": "x", "harness_type": "agent-os-v2", "harness_id": "h",
        "session_id": "s", "tick_id": "t", "agent_id": "main",
        "event_type": "tick_started", "data": {},
        "timestamp": "2026-07-22T00:00:00+00:00",
    }
    ev = ObserveEvent.from_dict(legacy)
    assert ev.parent_session_id == ""


def test_branch_created_constructor_carries_parent_and_agent_id() -> None:
    """branch_created 构造同时设 data.parent_branch_id + 顶层 parent_session_id + agent_id。"""
    from src.events import branch_created
    ev = branch_created(
        "agent-os-v2", "native_h", "child-sid",
        branch_id="child-sid", parent_branch_id="parent-sid",
        fork_tick_id="t1", agent_id="english-expert",
    )
    assert ev.event_type == EventType.BRANCH_CREATED
    assert ev.parent_session_id == "parent-sid"  # 顶层(F3,ws_ingest 直接读)
    assert ev.agent_id == "english-expert"       # 透传(ADR-1)
    assert ev.data["parent_branch_id"] == "parent-sid"  # wire contract 保留
    assert ev.data["branch_id"] == "child-sid"


def test_session_table_has_parent_session_id_column_legacy_null_safe(
    tmp_path: Path,
) -> None:
    """Migration adds parent_session_id; pre-existing rows keep NULL; idempotent."""
    db = tmp_path / "obs.db"
    import sqlite3
    conn = sqlite3.connect(str(db))
    # OLD schema: only agent_id (pre-F3), no parent_session_id. Legacy row.
    conn.executescript(
        """
        CREATE TABLE observe_sessions (
            harness_type TEXT NOT NULL, session_id TEXT NOT NULL,
            harness_id TEXT NOT NULL, created_at TEXT NOT NULL,
            last_active TEXT NOT NULL, agent_id TEXT,
            PRIMARY KEY (harness_type, session_id)
        );
        INSERT INTO observe_sessions VALUES ('claw','legacy-s','h','t','t',NULL);
        """
    )
    conn.commit()
    conn.close()

    store = SessionStore(db_path=db)
    # Legacy row readable, parent_session_id NULL.
    sess = store.get_session("claw", "legacy-s")
    assert sess is not None
    assert sess["parent_session_id"] is None

    # Re-init idempotency.
    store2 = SessionStore(db_path=db)
    assert store2.get_session("claw", "legacy-s")["parent_session_id"] is None
    store.close()
    store2.close()


def test_update_parent_session_id_backfills_only_when_null(tmp_path: Path) -> None:
    db = tmp_path / "obs.db"
    store = SessionStore(db_path=db)
    store.create_session("agent-os-v2", "child", "h1")  # parent NULL
    store.update_parent_session_id("agent-os-v2", "child", "parent-src")
    assert store.get_session("agent-os-v2", "child")["parent_session_id"] == "parent-src"

    # Non-destructive: backfill won't overwrite a session whose parent is already set.
    store.create_session("agent-os-v2", "child2", "h2")
    store.update_parent_session_id("agent-os-v2", "child2", "first-parent")
    store.update_parent_session_id("agent-os-v2", "child2", "other-parent")
    assert store.get_session("agent-os-v2", "child2")["parent_session_id"] == "first-parent"
    store.close()


def test_fork_tree_lineage_query(tmp_path: Path) -> None:
    """observe 能查 parent → children(fork 树可观测,F3 终态)。"""
    db = tmp_path / "obs.db"
    store = SessionStore(db_path=db)
    # parent session + 2 fork children(经 branch_created ws_ingest 回填)
    store.create_session("agent-os-v2", "parent", "h", agent_id="native")
    for child in ("child-a", "child-b"):
        store.create_session("agent-os-v2", child, "h", agent_id="native")
        store.update_parent_session_id("agent-os-v2", child, "parent")

    all_sessions = store.list_sessions("agent-os-v2")
    children = [s for s in all_sessions if s["parent_session_id"] == "parent"]
    assert {s["session_id"] for s in children} == {"child-a", "child-b"}
    # parent 自身 parent_session_id NULL(根节点)
    parent_row = next(s for s in all_sessions if s["session_id"] == "parent")
    assert parent_row["parent_session_id"] is None
    store.close()


def test_branch_created_wire_dict_round_trips_parent(tmp_path: Path) -> None:
    """真实 WS 链路回归:orchestrator emit 的 branch_created dict(含顶层
    parent_session_id 键,非 observe 构造)经 ObserveEvent.from_dict 后,
    ws_ingest 能取到 event.parent_session_id 回填 session 行。

    回归守护:skeptic 发现 orchestrator _base 不写顶层 parent_session_id 键,
    致 from_dict 解析恒空串、update_parent_session_id 永不触发。此测模拟
    真实 wire dict(orchestrator events.branch_created 的输出 shape)端到端。
    """
    # 模拟 orchestrator events.branch_created 的 wire 输出(顶层带 parent_session_id)
    wire = {
        "event_id": "e1", "harness_type": "agent-os-v2", "harness_id": "native_h",
        "session_id": "child-wire", "tick_id": "",
        "event_type": "branch_created",
        "data": {"branch_id": "child-wire", "parent_branch_id": "parent-wire",
                 "fork_tick_id": ""},
        "agent_id": "english-expert",
        "parent_session_id": "parent-wire",  # orchestrator 顶层键(必须存在)
        "timestamp": "2026-07-22T00:00:00+00:00",
    }
    ev = ObserveEvent.from_dict(wire)
    assert ev.parent_session_id == "parent-wire"  # from_dict 读到顶层键
    assert ev.agent_id == "english-expert"
    # 模拟 ws_ingest 回填逻辑(app.py:218-222)
    db = tmp_path / "obs.db"
    store = SessionStore(db_path=db)
    store.create_session("agent-os-v2", "child-wire", "native_h")
    if getattr(ev, "parent_session_id", ""):
        store.update_parent_session_id(
            "agent-os-v2", "child-wire", ev.parent_session_id
        )
    assert store.get_session("agent-os-v2", "child-wire")["parent_session_id"] == "parent-wire"
    store.close()
