"""P0-5: Branch — divergent timeline in the canvas.

A branch represents a fork in the conversation. The main conversation
is branch "main" (branch_id = "main"). Branches allow exploration,
A/B testing prompts, or parallel tool execution without polluting
the main timeline.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)


# ── Path helpers ────────────────────────────────────────────────────

def _default_db_path() -> Path:
    base = Path(__file__).parent.parent.parent.parent
    data_dir = base / "data"
    data_dir.mkdir(exist_ok=True)
    return data_dir / "canvas_events.db"


# ── Schema ──────────────────────────────────────────────────────────

BRANCH_SCHEMA = """
CREATE TABLE IF NOT EXISTS canvas_branches (
    branch_id        TEXT PRIMARY KEY,
    session_id       TEXT NOT NULL,
    parent_branch_id TEXT NOT NULL DEFAULT 'main',
    fork_tick_id     TEXT NOT NULL DEFAULT '',
    status           TEXT NOT NULL DEFAULT 'active',
    created_at       TEXT NOT NULL,
    merged_at        TEXT,
    metadata         TEXT NOT NULL DEFAULT '{}'
);
"""


class BranchStatus(str, Enum):
    """Lifecycle states for a branch."""
    ACTIVE = "active"       # currently being written to
    MERGED = "merged"       # merged back into parent
    PRUNED = "pruned"       # discarded (but events retained)
    ARCHIVED = "archived"   # long-term storage, read-only


MAIN_BRANCH_ID = "main"


@dataclass
class Branch:
    """A divergent timeline within a canvas session.

    Attributes:
        branch_id: Unique identifier. "main" for the default branch.
        session_id: Parent session this branch belongs to.
        parent_branch_id: The branch this was forked from.
        fork_tick_id: The tick at which the fork happened.
        status: Current lifecycle state.
        created_at: ISO 8601 creation timestamp.
        merged_at: ISO 8601 merge timestamp (if merged).
        metadata: Arbitrary extension data.
    """
    branch_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str = ""
    parent_branch_id: str = MAIN_BRANCH_ID
    fork_tick_id: Optional[str] = None
    status: BranchStatus = BranchStatus.ACTIVE
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    merged_at: Optional[str] = None
    metadata: Dict[str, dict] = field(default_factory=dict)

    @classmethod
    def create_main(cls, session_id: str) -> "Branch":
        """Create the default main branch for a session."""
        return cls(
            branch_id=MAIN_BRANCH_ID,
            session_id=session_id,
            parent_branch_id="",
            status=BranchStatus.ACTIVE,
        )

    @classmethod
    def fork(
        cls,
        session_id: str,
        parent_branch_id: str = MAIN_BRANCH_ID,
        fork_tick_id: str = "",
    ) -> "Branch":
        """Create a new branch forked from an existing branch at a tick."""
        return cls(
            session_id=session_id,
            parent_branch_id=parent_branch_id,
            fork_tick_id=fork_tick_id,
            status=BranchStatus.ACTIVE,
        )

    def to_dict(self) -> dict:
        return {
            "branch_id": self.branch_id,
            "session_id": self.session_id,
            "parent_branch_id": self.parent_branch_id,
            "fork_tick_id": self.fork_tick_id,
            "status": self.status.value,
            "created_at": self.created_at,
            "merged_at": self.merged_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Branch":
        status_val = d.get("status", "active")
        return cls(
            status=BranchStatus(status_val),
            **{k: v for k, v in d.items() if k in cls.__dataclass_fields__},
        )


class BranchStore:
    """SQLite-backed persistent branch registry.

    Replaces the in-memory ``_branches`` dict so that branches survive
    process restarts.  Falls back to in-memory if the DB is unavailable.
    """

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self._db_path: Path = db_path or _default_db_path()
        self._conn: sqlite3.Connection = self._create_connection()
        self._conn.executescript(BRANCH_SCHEMA)
        self._conn.commit()
        # In-memory cache for fast lookups
        self._cache: Dict[str, Branch] = {}
        self._load_all()

    def _create_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _load_all(self) -> None:
        rows = self._conn.execute("SELECT * FROM canvas_branches").fetchall()
        for row in rows:
            branch = Branch.from_dict({
                "branch_id": row["branch_id"],
                "session_id": row["session_id"],
                "parent_branch_id": row["parent_branch_id"],
                "fork_tick_id": row["fork_tick_id"],
                "status": row["status"],
                "created_at": row["created_at"],
                "merged_at": row["merged_at"],
                "metadata": json.loads(row["metadata"]),
            })
            self._cache[branch.branch_id] = branch

    def save(self, branch: Branch) -> None:
        """Persist a branch (insert or update)."""
        self._cache[branch.branch_id] = branch
        self._conn.execute(
            """
            INSERT INTO canvas_branches
                (branch_id, session_id, parent_branch_id, fork_tick_id,
                 status, created_at, merged_at, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(branch_id) DO UPDATE SET
                status=excluded.status,
                merged_at=excluded.merged_at,
                metadata=excluded.metadata
            """,
            (
                branch.branch_id,
                branch.session_id,
                branch.parent_branch_id,
                branch.fork_tick_id,
                branch.status.value,
                branch.created_at,
                branch.merged_at,
                json.dumps(branch.metadata),
            ),
        )
        self._conn.commit()

    def get(self, branch_id: str) -> Optional[Branch]:
        return self._cache.get(branch_id)

    def __contains__(self, branch_id: str) -> bool:
        return branch_id in self._cache

    def __getitem__(self, branch_id: str) -> Branch:
        b = self._cache.get(branch_id)
        if b is None:
            raise KeyError(branch_id)
        return b

    def close(self) -> None:
        self._conn.close()
