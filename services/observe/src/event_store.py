"""Event Store: SQLite-backed immutable event log.

参考 canvas/event_store.py 的写/读分离连接 + after_event_id 断点续传。
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.events import ObserveEvent


def _default_db_path() -> Path:
    """Default to data/observe_events.db in project root."""
    base = Path(__file__).parent.parent.parent.parent
    data_dir = base / "data"
    data_dir.mkdir(exist_ok=True)
    return data_dir / "observe_events.db"


# ── Schema ──────────────────────────────────────────────────────────

EVENT_SCHEMA = """
CREATE TABLE IF NOT EXISTS observe_events (
    event_id       TEXT PRIMARY KEY,
    harness_type   TEXT NOT NULL,
    harness_id     TEXT NOT NULL,
    session_id     TEXT NOT NULL,
    tick_id        TEXT NOT NULL DEFAULT '',
    event_type     TEXT NOT NULL,
    data           TEXT NOT NULL DEFAULT '{}',
    timestamp      TEXT NOT NULL,
    created_at     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_session_events ON observe_events(session_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_harness_events ON observe_events(harness_type, session_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_tick ON observe_events(tick_id);
"""


# ── Event Store ────────────────────────────────────────────────────

class EventStore:
    """SQLite event store for observe-service.

    Thread-safe for async usage via a single connection with WAL mode.
    Write/Read separation: dedicated read connections avoid blocking.
    """

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self._db_path: Path = db_path or _default_db_path()
        self._async_lock = asyncio.Lock()
        self._conn: sqlite3.Connection = self._create_connection()
        self._init_schema()

    def _create_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA cache=shared")
        return conn

    def _read_connection(self) -> sqlite3.Connection:
        """Short-lived read connection (caller must close)."""
        conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _init_schema(self) -> None:
        self._conn.executescript(EVENT_SCHEMA)
        # ADR-1 (Node D): add agent_id to observe_events. Idempotent — legacy
        # rows keep NULL (ADD COLUMN default NULL, no rebuild). try/except so an
        # already-present column (fresh re-init) is a no-op. Same pattern as
        # session_store.
        try:
            self._conn.execute(
                "ALTER TABLE observe_events ADD COLUMN agent_id TEXT"
            )
            self._conn.commit()
        except sqlite3.OperationalError:
            pass  # column already exists

    # ── Write ──────────────────────────────────────────────────────

    async def append(self, event: ObserveEvent) -> None:
        """Append a single event."""
        async with self._async_lock:
            self._conn.execute(
                """
                INSERT INTO observe_events
                    (event_id, harness_type, harness_id, session_id, tick_id, event_type, data, timestamp, created_at, agent_id)
                VALUES
                    (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.harness_type,
                    event.harness_id,
                    event.session_id,
                    event.tick_id,
                    event.event_type.value,
                    json.dumps(event.data, ensure_ascii=False),
                    event.timestamp,
                    datetime.now(timezone.utc).isoformat(),
                    getattr(event, "agent_id", "") or None,
                ),
            )
            self._conn.commit()

    async def append_many(self, events: List[ObserveEvent]) -> None:
        """Batch-append multiple events."""
        async with self._async_lock:
            now = datetime.now(timezone.utc).isoformat()
            rows = [
                (
                    e.event_id,
                    e.harness_type,
                    e.harness_id,
                    e.session_id,
                    e.tick_id,
                    e.event_type.value,
                    json.dumps(e.data, ensure_ascii=False),
                    e.timestamp,
                    now,
                    getattr(e, "agent_id", "") or None,
                )
                for e in events
            ]
            self._conn.executemany(
                """
                INSERT OR IGNORE INTO observe_events
                    (event_id, harness_type, harness_id, session_id, tick_id, event_type, data, timestamp, created_at, agent_id)
                VALUES
                    (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            self._conn.commit()

    # ── Query ───────────────────────────────────────────────────────

    def get_events(
        self,
        harness_type: str,
        session_id: str,
        after_event_id: str = "",
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        """Fetch events for a session, optionally after a cursor.

        Args:
            harness_type: Harness type filter.
            session_id: Session ID filter.
            after_event_id: Cursor for pagination (断点续传).
            limit: Max events to return.
        """
        conn = self._read_connection()
        try:
            if after_event_id:
                # Fetch timestamp of cursor event
                row = conn.execute(
                    "SELECT timestamp FROM observe_events WHERE event_id = ?",
                    (after_event_id,),
                ).fetchone()
                if not row:
                    # Cursor not found, return empty
                    return []
                after_timestamp = row["timestamp"]
                rows = conn.execute(
                    """
                    SELECT * FROM observe_events
                    WHERE harness_type = ? AND session_id = ? AND timestamp > ?
                    ORDER BY timestamp ASC
                    LIMIT ?
                    """,
                    (harness_type, session_id, after_timestamp, limit),
                ).fetchall()
            else:
                # tail:取最新 limit 条(DESC LIMIT)再正序返回。
                # 原来直接 ASC LIMIT 返回最旧的 N 条 —— 长生命周期 session(main:main 累积
                # 1000+ 条)永远只看到最旧历史,近期对话被截断、TUI 显示陈旧。改为返回
                # 最近 N 条按时间正序,聊天 UI 才能看到当前对话。
                rows = conn.execute(
                    """
                    SELECT * FROM (
                        SELECT * FROM observe_events
                        WHERE harness_type = ? AND session_id = ?
                        ORDER BY timestamp DESC
                        LIMIT ?
                    )
                    ORDER BY timestamp ASC
                    """,
                    (harness_type, session_id, limit),
                ).fetchall()
            return [self._row_to_event_dict(row) for row in rows]
        finally:
            conn.close()

    def get_tick_events(
        self,
        harness_type: str,
        session_id: str,
        tick_id: str,
    ) -> List[Dict[str, Any]]:
        """Fetch all events for a specific tick."""
        conn = self._read_connection()
        try:
            rows = conn.execute(
                """
                SELECT * FROM observe_events
                WHERE harness_type = ? AND session_id = ? AND tick_id = ?
                ORDER BY timestamp ASC
                """,
                (harness_type, session_id, tick_id),
            ).fetchall()
            return [self._row_to_event_dict(row) for row in rows]
        finally:
            conn.close()

    # ── Utility ─────────────────────────────────────────────────────

    def _row_to_event_dict(self, row: sqlite3.Row) -> Dict[str, Any]:
        d = dict(row)
        d["data"] = json.loads(d["data"])
        return d

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "EventStore":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()
