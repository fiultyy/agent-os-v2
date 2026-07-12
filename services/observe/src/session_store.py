"""Session Registry: Composite key (harness_type, session_id) with sqlite persistence.

真实 session 管理：创建 / 列表(按 harness 分组) / 切换 / 持久化。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional


def _default_db_path() -> Path:
    """Default to data/observe_events.db in project root."""
    base = Path(__file__).parent.parent.parent.parent  # services/observe -> project root
    data_dir = base / "data"
    data_dir.mkdir(exist_ok=True)
    return data_dir / "observe_events.db"


# ── Schema ──────────────────────────────────────────────────────────

SESSION_SCHEMA = """
CREATE TABLE IF NOT EXISTS observe_sessions (
    harness_type    TEXT NOT NULL,
    session_id      TEXT NOT NULL,
    harness_id      TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    last_active     TEXT NOT NULL,
    PRIMARY KEY (harness_type, session_id)
);

CREATE INDEX IF NOT EXISTS idx_harness_type ON observe_sessions(harness_type, last_active);
"""


# ── Session Store ────────────────────────────────────────────────────

class SessionStore:
    """Session registry for observe-service.

    Thread-safe for async usage via a single connection with WAL mode.
    Composite key: (harness_type, session_id).
    """

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self._db_path: Path = db_path or _default_db_path()
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

    def _init_schema(self) -> None:
        self._conn.executescript(SESSION_SCHEMA)
        self._conn.commit()

    # ── CRUD ────────────────────────────────────────────────────────

    def create_session(
        self,
        harness_type: str,
        session_id: str,
        harness_id: str,
    ) -> None:
        """Register a new session."""
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            """
            INSERT OR REPLACE INTO observe_sessions
                (harness_type, session_id, harness_id, created_at, last_active)
            VALUES (?, ?, ?, ?, ?)
            """,
            (harness_type, session_id, harness_id, now, now),
        )
        self._conn.commit()

    def update_last_active(self, harness_type: str, session_id: str) -> None:
        """Update session last_active timestamp."""
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            """
            UPDATE observe_sessions
            SET last_active = ?
            WHERE harness_type = ? AND session_id = ?
            """,
            (now, harness_type, session_id),
        )
        self._conn.commit()

    def get_session(
        self,
        harness_type: str,
        session_id: str,
    ) -> Optional[Dict[str, str]]:
        """Fetch a single session."""
        row = self._conn.execute(
            """
            SELECT * FROM observe_sessions
            WHERE harness_type = ? AND session_id = ?
            """,
            (harness_type, session_id),
        ).fetchone()
        return dict(row) if row else None

    def list_sessions(self, harness_type: Optional[str] = None) -> List[Dict[str, str]]:
        """List all sessions, optionally filtered by harness_type.

        Returns list of sessions ordered by last_active DESC.
        """
        if harness_type:
            rows = self._conn.execute(
                """
                SELECT * FROM observe_sessions
                WHERE harness_type = ?
                ORDER BY last_active DESC
                """,
                (harness_type,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                """
                SELECT * FROM observe_sessions
                ORDER BY last_active DESC
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def list_by_harness_group(self) -> Dict[str, List[Dict[str, str]]]:
        """List sessions grouped by harness_type (for UI dropdown)."""
        sessions = self.list_sessions()
        grouped: Dict[str, List[Dict[str, str]]] = {}
        for sess in sessions:
            ht = sess["harness_type"]
            if ht not in grouped:
                grouped[ht] = []
            grouped[ht].append(sess)
        return grouped

    def delete_session(self, harness_type: str, session_id: str) -> bool:
        """Delete a session. Returns True if deleted."""
        cursor = self._conn.execute(
            """
            DELETE FROM observe_sessions
            WHERE harness_type = ? AND session_id = ?
            """,
            (harness_type, session_id),
        )
        self._conn.commit()
        return cursor.rowcount > 0

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "SessionStore":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
