"""SQLite-backed memory store — persistent storage for agent memories.

Provides :class:`SQLiteStore` with the same interface as :class:`InMemoryStore`,
backed by a single SQLite database file. Uses WAL mode for concurrent read/write
performance and stores metadata as JSON.

Data files:
- ``data/memories.db`` — memories, blocks, sessions, and version history.
"""

from __future__ import annotations

import json
import uuid

try:
    from pysqlite3 import dbapi2 as sqlite3  # type: ignore[import-untyped]
except ImportError:
    import sqlite3  # noqa: F401 — stdlib fallback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.memory.types import (
    MemoryItem,
    MemoryBlock,
    MemoryFilter,
    MemoryType,
    MemoryScope,
    MemoryOrigin,
    MemoryState,
)


class SQLiteStore:
    """SQLite-backed persistent store for agent memories and blocks.

    All data is persisted to a single ``.db`` file. The store is safe for
    concurrent reads (WAL mode) but assumes a single writer process.

    Args:
        db_path: Path to the SQLite database file. Parent directories are
            created automatically. Defaults to ``"data/memories.db"``.
    """

    def __init__(self, db_path: str = "data/memories.db") -> None:
        self._db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.row_factory = sqlite3.Row

        self._create_tables()

    # ── Schema ────────────────────────────────────────────────────────

    def _create_tables(self) -> None:
        """Create database tables if they do not exist."""
        with self._conn:
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY,
                    content TEXT NOT NULL,
                    scope TEXT,
                    memory_type TEXT,
                    importance REAL DEFAULT 0.0,
                    metadata TEXT,
                    agent_id TEXT DEFAULT '',
                    session_id TEXT DEFAULT '',
                    created_at TEXT,
                    accessed_at TEXT,
                    updated_at TEXT,
                    archived INTEGER DEFAULT 0,
                    origin TEXT DEFAULT 'foreground',
                    state TEXT DEFAULT 'active',
                    last_state_transition TEXT DEFAULT ''
                )
            """)
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS memory_blocks (
                    id TEXT PRIMARY KEY,
                    agent_id TEXT NOT NULL,
                    label TEXT NOT NULL,
                    content TEXT DEFAULT '',
                    char_limit INTEGER DEFAULT 2000,
                    updated_at TEXT,
                    UNIQUE(agent_id, label)
                )
            """)
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS memory_versions (
                    id TEXT PRIMARY KEY,
                    memory_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    content TEXT,
                    metadata TEXT,
                    operator TEXT DEFAULT '',
                    created_at TEXT,
                    FOREIGN KEY (memory_id) REFERENCES memories(id)
                )
            """)
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    agent_id TEXT NOT NULL,
                    status TEXT DEFAULT 'active',
                    message_count INTEGER DEFAULT 0
                )
            """)
            # Index for incremental change detection (MemoryDBWatcher watermark).
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_memories_updated_at ON memories(updated_at)"
            )
        self._migrate_schema()

    def _migrate_schema(self) -> None:
        """Apply incremental schema changes for older databases.

        Adds ``origin`` (P0) and ``state`` + ``last_state_transition`` (P3
        state machine) columns to ``memories`` if missing. Existing rows
        backfill origin→'foreground', state→'active' (archived rows→
        'archived'). Wrapped in a transaction so all ALTERs commit atomically.
        """
        with self._conn:
            existing_cols = {
                row[1]
                for row in self._conn.execute("PRAGMA table_info(memories)").fetchall()
            }
            if "origin" not in existing_cols:
                self._conn.execute(
                    "ALTER TABLE memories ADD COLUMN origin TEXT DEFAULT 'foreground'"
                )
            # P3 state machine: deterministic lifecycle columns. Backfill legacy
            # archived rows to ARCHIVED so the new state field matches the old flag.
            if "state" not in existing_cols:
                self._conn.execute(
                    "ALTER TABLE memories ADD COLUMN state TEXT DEFAULT 'active'"
                )
                self._conn.execute(
                    "UPDATE memories SET state = 'archived' WHERE archived = 1"
                )
            if "last_state_transition" not in existing_cols:
                self._conn.execute(
                    "ALTER TABLE memories ADD COLUMN last_state_transition TEXT DEFAULT ''"
                )

    def max_updated_at(self) -> str | None:
        """Return the maximum ``updated_at`` over all memories, or ``None``.

        Used by :class:`~src.memory.db_watcher.MemoryDBWatcher` for
        incremental change detection (external DB writes). Backed by
        ``idx_memories_updated_at`` so it is an index-only scan. Returns
        ``None`` for an empty table.
        """
        row = self._conn.execute(
            "SELECT MAX(updated_at) FROM memories"
        ).fetchone()
        return row[0] if row else None

    # ── Serialization helpers ─────────────────────────────────────────

    @staticmethod
    def _item_to_row(item: MemoryItem) -> dict[str, Any]:
        """Convert a MemoryItem to a dict suitable for INSERT/UPDATE."""
        return {
            "id": item.id,
            "content": item.content,
            "scope": item.scope.value if isinstance(item.scope, MemoryScope) else item.scope,
            "memory_type": item.memory_type.value if isinstance(item.memory_type, MemoryType) else item.memory_type,
            "importance": item.importance,
            "metadata": json.dumps(item.metadata, ensure_ascii=False),
            "agent_id": item.agent_id,
            "session_id": item.session_id,
            "created_at": item.created_at,
            "accessed_at": item.accessed_at,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "archived": 1 if item.archived else 0,
            "origin": item.origin.value if isinstance(item.origin, MemoryOrigin) else item.origin,
            "state": item.state.value if isinstance(item.state, MemoryState) else item.state,
            "last_state_transition": item.last_state_transition,
        }

    @staticmethod
    def _row_to_item(row: sqlite3.Row) -> MemoryItem:
        """Convert a database row to a MemoryItem."""
        meta_raw = row["metadata"]
        metadata: dict[str, Any] = json.loads(meta_raw) if meta_raw else {}
        return MemoryItem(
            id=row["id"],
            content=row["content"],
            memory_type=MemoryType(row["memory_type"]) if row["memory_type"] else MemoryType.WORKING,
            scope=MemoryScope(row["scope"]) if row["scope"] else MemoryScope.AGENT,
            importance=row["importance"] or 0.0,
            metadata=metadata,
            agent_id=row["agent_id"] or "",
            session_id=row["session_id"] or "",
            created_at=row["created_at"] or "",
            accessed_at=row["accessed_at"] or "",
            archived=bool(row["archived"]),
            origin=MemoryOrigin(row["origin"]) if row["origin"] else MemoryOrigin.FOREGROUND,
            state=MemoryState(row["state"]) if row["state"] else MemoryState.ACTIVE,
            last_state_transition=row["last_state_transition"] or "",
        )

    # ── Memory Item CRUD ──────────────────────────────────────────────

    async def store(self, item: MemoryItem) -> str:
        """Store a memory item. Assigns an ID if none provided.

        Args:
            item: The memory item to persist.

        Returns:
            The item's unique ID.
        """
        if not item.id:
            item.id = str(uuid.uuid4())
        row = self._item_to_row(item)
        with self._conn:
            self._conn.execute(
                """INSERT OR REPLACE INTO memories
                   (id, content, scope, memory_type, importance, metadata,
                    agent_id, session_id, created_at, accessed_at, updated_at, archived, origin,
                    state, last_state_transition)
                   VALUES (:id, :content, :scope, :memory_type, :importance, :metadata,
                    :agent_id, :session_id, :created_at, :accessed_at, :updated_at, :archived, :origin,
                    :state, :last_state_transition)""",
                row,
            )
        return item.id

    async def get(self, item_id: str) -> MemoryItem | None:
        """Retrieve a memory item by ID, or ``None`` if not found."""
        row = self._conn.execute(
            "SELECT * FROM memories WHERE id = ?", (item_id,)
        ).fetchone()
        if row is None:
            return None
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "UPDATE memories SET accessed_at = ? WHERE id = ?", (now, item_id)
        )
        return self._row_to_item(row)

    async def update(self, item_id: str, content: str | None = None, **kwargs: Any) -> MemoryItem | None:
        """Update a memory item's content and/or fields.

        Args:
            item_id: ID of the item to update.
            content: New content text (optional).
            **kwargs: Additional fields to update.

        Returns:
            The updated item, or ``None`` if not found.
        """
        row = self._conn.execute(
            "SELECT * FROM memories WHERE id = ?", (item_id,)
        ).fetchone()
        if row is None:
            return None

        item = self._row_to_item(row)
        if content is not None:
            item.content = content
        for key, value in kwargs.items():
            if hasattr(item, key):
                setattr(item, key, value)
        item.accessed_at = datetime.now(timezone.utc).isoformat()

        updated = self._item_to_row(item)
        with self._conn:
            self._conn.execute(
                """UPDATE memories SET
                   content = :content, scope = :scope, memory_type = :memory_type,
                   importance = :importance, metadata = :metadata,
                   agent_id = :agent_id, session_id = :session_id,
                   accessed_at = :accessed_at, updated_at = :updated_at,
                   archived = :archived, origin = :origin,
                   state = :state, last_state_transition = :last_state_transition
                   WHERE id = :id""",
                updated,
            )
        return item

    async def delete(self, item_id: str) -> bool:
        """Delete a memory item by ID.

        Returns:
            ``True`` if the item existed and was deleted.
        """
        with self._conn:
            cursor = self._conn.execute(
                "DELETE FROM memories WHERE id = ?", (item_id,)
            )
        return cursor.rowcount > 0

    async def list_by_scope(
        self,
        scope: MemoryScope,
        agent_id: str = "",
        limit: int = 100,
    ) -> list[MemoryItem]:
        """List memory items filtered by scope and optionally agent.

        Args:
            scope: Trust-domain scope to filter by.
            agent_id: Optional agent filter.
            limit: Maximum number of items to return.

        Returns:
            List of matching memory items.
        """
        query = "SELECT * FROM memories WHERE scope = ? AND archived = 0"
        params: list[Any] = [scope.value]
        if agent_id:
            query += " AND agent_id = ?"
            params.append(agent_id)
        query += " LIMIT ?"
        params.append(limit)

        rows = self._conn.execute(query, params).fetchall()
        return [self._row_to_item(r) for r in rows]

    async def search(self, filter: MemoryFilter) -> list[MemoryItem]:
        """Search memory items using a :class:`MemoryFilter`.

        Returns all items matching every non-empty filter criterion.
        Results are sorted by creation time (newest first).
        """
        clauses: list[str] = []
        params: list[Any] = []

        if filter.agent_id:
            clauses.append("agent_id = ?")
            params.append(filter.agent_id)
        if filter.session_id:
            clauses.append("session_id = ?")
            params.append(filter.session_id)
        if filter.memory_type:
            clauses.append("memory_type = ?")
            params.append(filter.memory_type.value)
        if filter.scope:
            clauses.append("scope = ?")
            params.append(filter.scope.value)
        if filter.origin:
            clauses.append("origin = ?")
            params.append(filter.origin.value)
        if filter.state:
            clauses.append("state = ?")
            params.append(filter.state.value)
        if filter.keyword:
            clauses.append("content LIKE ?")
            params.append(f"%{filter.keyword}%")
        if filter.min_importance:
            clauses.append("importance >= ?")
            params.append(filter.min_importance)
        if not filter.archived:
            clauses.append("archived = 0")

        where = " AND ".join(clauses) if clauses else "1=1"
        query = f"SELECT * FROM memories WHERE {where} ORDER BY created_at DESC"

        rows = self._conn.execute(query, params).fetchall()
        return [self._row_to_item(r) for r in rows]

    # ── Memory Block Management ───────────────────────────────────────

    async def create_block(
        self,
        agent_id: str,
        label: str,
        char_limit: int = 2000,
        initial_content: str = "",
    ) -> MemoryBlock:
        """Create a new memory block for an agent.

        Args:
            agent_id: Owning agent.
            label: Block label.
            char_limit: Maximum character count.
            initial_content: Optional starting content.

        Returns:
            The newly created block.
        """
        block_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        with self._conn:
            self._conn.execute(
                """INSERT INTO memory_blocks (id, agent_id, label, content, char_limit, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (block_id, agent_id, label, initial_content, char_limit, now),
            )
        return MemoryBlock(
            label=label,
            content=initial_content,
            char_limit=char_limit,
            agent_id=agent_id,
        )

    async def get_block(self, agent_id: str, label: str) -> MemoryBlock | None:
        """Read a memory block by agent and label."""
        row = self._conn.execute(
            "SELECT * FROM memory_blocks WHERE agent_id = ? AND label = ?",
            (agent_id, label),
        ).fetchone()
        if row is None:
            return None
        return MemoryBlock(
            label=row["label"],
            content=row["content"] or "",
            char_limit=row["char_limit"],
            agent_id=row["agent_id"],
        )

    async def update_block(self, agent_id: str, label: str, content: str) -> MemoryBlock | None:
        """Update a block's content. Validates char_limit.

        Returns:
            The updated block, or ``None`` if not found.

        Raises:
            ValueError: If content exceeds the block's char_limit.
        """
        row = self._conn.execute(
            "SELECT * FROM memory_blocks WHERE agent_id = ? AND label = ?",
            (agent_id, label),
        ).fetchone()
        if row is None:
            return None
        if len(content) > row["char_limit"]:
            raise ValueError(
                f"Content ({len(content)} chars) exceeds block limit "
                f"({row['char_limit']} chars) for block {label!r}"
            )
        now = datetime.now(timezone.utc).isoformat()
        with self._conn:
            self._conn.execute(
                "UPDATE memory_blocks SET content = ?, updated_at = ? WHERE agent_id = ? AND label = ?",
                (content, now, agent_id, label),
            )
        return MemoryBlock(
            label=label,
            content=content,
            char_limit=row["char_limit"],
            agent_id=agent_id,
        )

    async def list_blocks(self, agent_id: str) -> list[MemoryBlock]:
        """List all blocks belonging to an agent."""
        rows = self._conn.execute(
            "SELECT * FROM memory_blocks WHERE agent_id = ?", (agent_id,)
        ).fetchall()
        return [
            MemoryBlock(
                label=r["label"],
                content=r["content"] or "",
                char_limit=r["char_limit"],
                agent_id=r["agent_id"],
            )
            for r in rows
        ]

    # ── Session Lifecycle ─────────────────────────────────────────────

    async def create_session(self, session_id: str, agent_id: str) -> dict[str, Any]:
        """Create a new session state.

        Args:
            session_id: Unique session identifier.
            agent_id: Agent participating in this session.

        Returns:
            The session state dictionary.
        """
        with self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO sessions (id, agent_id, status, message_count) VALUES (?, ?, 'active', 0)",
                (session_id, agent_id),
            )
        return {
            "id": session_id,
            "agent_id": agent_id,
            "status": "active",
            "message_count": 0,
        }

    async def append_to_session(self, session_id: str, message: dict[str, Any]) -> None:
        """Append a message to the session and increment counter."""
        with self._conn:
            self._conn.execute(
                "UPDATE sessions SET message_count = message_count + 1 WHERE id = ?",
                (session_id,),
            )

    async def get_session(self, session_id: str) -> dict[str, Any] | None:
        """Retrieve session state."""
        row = self._conn.execute(
            "SELECT * FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if row is None:
            return None
        return {
            "id": row["id"],
            "agent_id": row["agent_id"],
            "status": row["status"],
            "message_count": row["message_count"],
        }

    async def get_recent(
        self,
        session_id: str,
        limit: int = 5,
    ) -> list[MemoryItem]:
        """Get the most recent memory items for a session.

        Args:
            session_id: Session to query.
            limit: Maximum items to return.

        Returns:
            List of recent memory items sorted by creation time.
        """
        rows = self._conn.execute(
            "SELECT * FROM memories WHERE session_id = ? AND archived = 0 ORDER BY created_at DESC LIMIT ?",
            (session_id, limit),
        ).fetchall()
        return [self._row_to_item(r) for r in rows]

    async def archive_session(self, session_id: str) -> bool:
        """Archive a session: mark status and archive all its memories.

        Returns:
            ``True`` if the session existed and was archived.
        """
        row = self._conn.execute(
            "SELECT * FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if row is None:
            return False
        with self._conn:
            self._conn.execute(
                "UPDATE sessions SET status = 'archived' WHERE id = ?",
                (session_id,),
            )
            self._conn.execute(
                "UPDATE memories SET archived = 1 WHERE session_id = ?",
                (session_id,),
            )
        return True

    async def destroy_session(self, session_id: str) -> bool:
        """Destroy a session and remove all its memory items.

        Returns:
            ``True`` if the session existed.
        """
        row = self._conn.execute(
            "SELECT * FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if row is None:
            return False
        with self._conn:
            self._conn.execute(
                "DELETE FROM memories WHERE session_id = ?", (session_id,)
            )
            self._conn.execute(
                "DELETE FROM sessions WHERE id = ?", (session_id,)
            )
        return True
