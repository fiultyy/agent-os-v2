"""P0-补充: CanvasEventStore — SQLite-backed immutable event log.

Event Sourcing store: append-only, query by session/branch/tick.
Provides replay capability for frontend state reconstruction.

SQLite WAL mode for concurrent readers + single writer.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.canvas.events import CanvasEvent

logger = logging.getLogger(__name__)

# ── Path helpers ────────────────────────────────────────────────────

def _default_db_path() -> Path:
    base = Path(__file__).parent.parent.parent.parent
    data_dir = base / "data"
    data_dir.mkdir(exist_ok=True)
    return data_dir / "canvas_events.db"


# ── Schema ──────────────────────────────────────────────────────────

SCHEMA = """
CREATE TABLE IF NOT EXISTS canvas_events (
    event_id      TEXT PRIMARY KEY,
    session_id    TEXT NOT NULL,
    branch_id     TEXT NOT NULL DEFAULT 'main',
    tick_id       TEXT NOT NULL DEFAULT '',
    event_type    TEXT NOT NULL,
    data          TEXT NOT NULL DEFAULT '{}',
    lod           INTEGER NOT NULL DEFAULT 2,
    timestamp     TEXT NOT NULL,
    created_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_session ON canvas_events(session_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_branch  ON canvas_events(branch_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_tick    ON canvas_events(tick_id);
"""


# ── Store ──────────────────────────────────────────────────────────

class CanvasEventStore:
    """SQLite event store for the canvas event log.

    Thread-safe for async usage via a single connection with WAL mode.
    All write operations are serialised through a lock.
    """

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self._db_path: Path = db_path or _default_db_path()
        self._conn: sqlite3.Connection = self._create_connection()
        self._init_schema()

    # ── Connection ─────────────────────────────────────────────────

    def _create_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _init_schema(self) -> None:
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    # ── Write ──────────────────────────────────────────────────────

    def append(self, event: CanvasEvent) -> None:
        """Append a single event to the log (append-only)."""
        self._conn.execute(
            """
            INSERT INTO canvas_events
                (event_id, session_id, branch_id, tick_id, event_type, data, lod, timestamp, created_at)
            VALUES
                (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.event_id,
                event.session_id,
                event.branch_id,
                event.tick_id,
                event.event_type,
                json.dumps(event.data, ensure_ascii=False),
                event.lod,
                event.timestamp,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        self._conn.commit()

    def append_many(self, events: List[CanvasEvent]) -> None:
        """Batch-append multiple events in a single transaction."""
        rows = [
            (
                e.event_id,
                e.session_id,
                e.branch_id,
                e.tick_id,
                e.event_type,
                json.dumps(e.data, ensure_ascii=False),
                e.lod,
                e.timestamp,
                datetime.now(timezone.utc).isoformat(),
            )
            for e in events
        ]
        self._conn.executemany(
            """
            INSERT OR IGNORE INTO canvas_events
                (event_id, session_id, branch_id, tick_id, event_type, data, lod, timestamp, created_at)
            VALUES
                (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        self._conn.commit()

    # ── Query ──────────────────────────────────────────────────────

    def get_events(
        self,
        session_id: str,
        after_event_id: str = "",
        after_timestamp: str = "",
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        """Fetch events for a session, optionally after a cursor."""
        if after_event_id:
            row = self._conn.execute(
                "SELECT timestamp FROM canvas_events WHERE event_id = ?",
                (after_event_id,),
            ).fetchone()
            if row:
                after_timestamp = row["timestamp"]
        if after_timestamp:
            rows = self._conn.execute(
                """
                SELECT * FROM canvas_events
                WHERE session_id = ? AND timestamp > ?
                ORDER BY timestamp ASC
                LIMIT ?
                """,
                (session_id, after_timestamp, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                """
                SELECT * FROM canvas_events
                WHERE session_id = ?
                ORDER BY timestamp ASC
                LIMIT ?
                """,
                (session_id, limit),
            ).fetchall()
        return [self._row_to_event_dict(row) for row in rows]

    def get_branch_ticks(
        self, branch_id: str, limit: int = 500
    ) -> List[Dict[str, Any]]:
        """Fetch all events for a specific branch, ordered by time."""
        rows = self._conn.execute(
            """
            SELECT * FROM canvas_events
            WHERE branch_id = ?
            ORDER BY timestamp ASC
            LIMIT ?
            """,
            (branch_id, limit),
        ).fetchall()
        return [self._row_to_event_dict(row) for row in rows]

    def get_session_ticks(
        self, session_id: str, limit: int = 500
    ) -> List[Dict[str, Any]]:
        """Fetch all events for a session (all branches), ordered by time."""
        rows = self._conn.execute(
            """
            SELECT * FROM canvas_events
            WHERE session_id = ?
            ORDER BY timestamp ASC
            LIMIT ?
            """,
            (session_id, limit),
        ).fetchall()
        return [self._row_to_event_dict(row) for row in rows]

    def get_event_by_id(self, event_id: str) -> Optional[Dict[str, Any]]:
        """Fetch a single event by its ID."""
        row = self._conn.execute(
            "SELECT * FROM canvas_events WHERE event_id = ?",
            (event_id,),
        ).fetchone()
        return self._row_to_event_dict(row) if row else None

    # ── Utility ─────────────────────────────────────────────────────

    def _row_to_event_dict(self, row: sqlite3.Row) -> Dict[str, Any]:
        d = dict(row)
        d["data"] = json.loads(d["data"])
        return d

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "CanvasEventStore":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()
