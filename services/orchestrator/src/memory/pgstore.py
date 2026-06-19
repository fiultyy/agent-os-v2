"""PostgreSQL-backed memory store for production use.

Replaces :class:`InMemoryStore` with a durable, concurrent-safe
database backend using SQLAlchemy Core (async) for maximum performance.

Features:
- Full CRUD on memory items and blocks.
- Session lifecycle management.
- Connection pooling via SQLAlchemy async engine.
- Vector search integration (delegates to VectorStore).
- Alembic-compatible migration schema.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from src.memory.types import (
    MemoryBlock,
    MemoryFilter,
    MemoryItem,
    MemoryOrigin,
    MemoryScope,
    MemoryType,
)

logger = logging.getLogger(__name__)

# ── Table definitions (SQLAlchemy Core) ────────────────────────────────

metadata = sa.MetaData()

memory_items = sa.Table(
    "memory_items",
    metadata,
    sa.Column("id", sa.String(36), primary_key=True),
    sa.Column("agent_id", sa.String(36), nullable=False),
    sa.Column("session_id", sa.String(36), nullable=False, server_default=""),
    sa.Column("memory_type", sa.String(20), nullable=False),
    sa.Column("scope", sa.String(20), nullable=False),
    sa.Column("content", sa.Text, nullable=False),
    sa.Column("importance", sa.Float, nullable=False, server_default="0.5"),
    sa.Column("metadata_json", sa.Text, nullable=False, server_default="{}"),
    sa.Column("created_at", sa.String(40), nullable=False),
    sa.Column("accessed_at", sa.String(40), nullable=False),
    sa.Column("origin", sa.Text, nullable=False, server_default="foreground"),
    sa.Column("archived", sa.Boolean, nullable=False, server_default=sa.text("false")),
    sa.Index("ix_memory_agent_id", "agent_id"),
    sa.Index("ix_memory_session_id", "session_id"),
)

memory_blocks = sa.Table(
    "memory_blocks",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("agent_id", sa.String(36), nullable=False),
    sa.Column("label", sa.String(100), nullable=False),
    sa.Column("content", sa.Text, nullable=False, server_default=""),
    sa.Column("char_limit", sa.Integer, nullable=False, server_default="2000"),
    sa.UniqueConstraint("agent_id", "label", name="uq_block_agent_label"),
    sa.Index("ix_blocks_agent_id", "agent_id"),
)

sessions = sa.Table(
    "sessions",
    metadata,
    sa.Column("id", sa.String(36), primary_key=True),
    sa.Column("agent_id", sa.String(36), nullable=False),
    sa.Column("status", sa.String(20), nullable=False, server_default="active"),
    sa.Column("message_count", sa.Integer, nullable=False, server_default="0"),
    sa.Column("created_at", sa.String(40), nullable=False),
)

agents = sa.Table(
    "agents",
    metadata,
    sa.Column("id", sa.String(36), primary_key=True),
    sa.Column("name", sa.String(200), nullable=False, server_default="New Agent"),
    sa.Column("description", sa.Text, nullable=False, server_default=""),
    sa.Column("status", sa.String(20), nullable=False, server_default="idle"),
    sa.Column("model", sa.String(100), nullable=False, server_default="gpt-4o-mini"),
    sa.Column("tools_json", sa.Text, nullable=False, server_default="[]"),
    sa.Column("created_at", sa.String(40), nullable=False),
    sa.Column("updated_at", sa.String(40), nullable=False),
)


# ── Helper ─────────────────────────────────────────────────────────────


def _row_to_memory_item(row: sa.Row) -> MemoryItem:
    """Convert a database row to a MemoryItem dataclass."""
    meta_raw = row[memory_items.c.metadata_json]
    metadata_dict: dict[str, Any] = json.loads(meta_raw) if isinstance(meta_raw, str) else {}

    return MemoryItem(
        id=row[memory_items.c.id],
        agent_id=row[memory_items.c.agent_id],
        session_id=row[memory_items.c.session_id],
        memory_type=MemoryType(row[memory_items.c.memory_type]),
        scope=MemoryScope(row[memory_items.c.scope]),
        content=row[memory_items.c.content],
        importance=float(row[memory_items.c.importance]),
        metadata=metadata_dict,
        created_at=row[memory_items.c.created_at],
        accessed_at=row[memory_items.c.accessed_at],
        origin=MemoryOrigin(row[memory_items.c.origin]) if row[memory_items.c.origin] else MemoryOrigin.FOREGROUND,
        archived=bool(row[memory_items.c.archived]),
    )


def _row_to_block(row: sa.Row) -> MemoryBlock:
    """Convert a database row to a MemoryBlock dataclass."""
    return MemoryBlock(
        label=row[memory_blocks.c.label],
        content=row[memory_blocks.c.content],
        char_limit=int(row[memory_blocks.c.char_limit]),
        agent_id=row[memory_blocks.c.agent_id],
    )


def _row_to_agent(row: sa.Row) -> dict[str, Any]:
    """Convert a database row to an agent dictionary."""
    tools_raw = row[agents.c.tools_json]
    tools_list: list[str] = json.loads(tools_raw) if isinstance(tools_raw, str) else []
    return {
        "id": row[agents.c.id],
        "name": row[agents.c.name],
        "description": row[agents.c.description],
        "status": row[agents.c.status],
        "model": row[agents.c.model],
        "tools": tools_list,
        "created_at": row[agents.c.created_at],
        "updated_at": row[agents.c.updated_at],
    }


# ── PostgresStore ──────────────────────────────────────────────────────


class PostgresStore:
    """PostgreSQL-backed memory store.

    Usage::

        store = PostgresStore("postgresql+asyncpg://user:pass@localhost/agentos")
        await store.initialize()

        # Now use like InMemoryStore
        item_id = await store.store(memory_item)
        item = await store.get(item_id)
    """

    def __init__(self, database_url: str, pool_size: int = 10) -> None:
        self._engine: AsyncEngine = create_async_engine(
            database_url,
            pool_size=pool_size,
            max_overflow=5,
            pool_pre_ping=True,
        )
        self._initialized = False

    async def initialize(self) -> None:
        """Create tables if they don't exist (for development).

        In production, use Alembic migrations instead.
        """
        if self._initialized:
            return
        async with self._engine.begin() as conn:
            await conn.run_sync(metadata.create_all)
        self._initialized = True
        logger.info("PostgresStore initialized (tables ensured)")

    async def close(self) -> None:
        """Dispose of the connection pool."""
        await self._engine.dispose()

    # ── Memory Item CRUD ──────────────────────────────────────────

    async def store(self, item: MemoryItem) -> str:
        """Store a memory item. Assigns an ID if none provided."""
        if not item.id:
            item.id = str(uuid.uuid4())

        async with self._engine.begin() as conn:
            await conn.execute(
                memory_items.insert().values(
                    id=item.id,
                    agent_id=item.agent_id,
                    session_id=item.session_id,
                    memory_type=item.memory_type.value,
                    scope=item.scope.value,
                    content=item.content,
                    importance=item.importance,
                    metadata_json=json.dumps(item.metadata, default=str),
                    created_at=item.created_at,
                    accessed_at=item.accessed_at,
                    origin=item.origin.value,
                    archived=item.archived,
                )
            )
        return item.id

    async def get(self, item_id: str) -> MemoryItem | None:
        """Retrieve a memory item by ID."""
        async with self._engine.connect() as conn:
            result = await conn.execute(
                memory_items.select().where(memory_items.c.id == item_id)
            )
            row = result.fetchone()
            if row is None:
                return None

            # Update accessed_at
            now = datetime.now(timezone.utc).isoformat()
            await conn.execute(
                memory_items.update()
                .where(memory_items.c.id == item_id)
                .values(accessed_at=now)
            )
            await conn.commit()
            return _row_to_memory_item(row)

    async def update(self, item_id: str, content: str | None = None, **kwargs: Any) -> MemoryItem | None:
        """Update a memory item's content and/or fields."""
        async with self._engine.begin() as conn:
            # Check existence
            result = await conn.execute(
                memory_items.select().where(memory_items.c.id == item_id)
            )
            row = result.fetchone()
            if row is None:
                return None

            values: dict[str, Any] = {
                "accessed_at": datetime.now(timezone.utc).isoformat(),
            }
            if content is not None:
                values["content"] = content

            # Map kwargs to column names
            field_map = {
                "importance": "importance",
                "archived": "archived",
                "agent_id": "agent_id",
                "session_id": "session_id",
            }
            for attr, col in field_map.items():
                if attr in kwargs:
                    values[col] = kwargs[attr]

            if "origin" in kwargs:
                origin_val = kwargs["origin"]
                values["origin"] = origin_val.value if isinstance(origin_val, MemoryOrigin) else origin_val

            if "metadata" in kwargs:
                values["metadata_json"] = json.dumps(kwargs["metadata"], default=str)

            await conn.execute(
                memory_items.update()
                .where(memory_items.c.id == item_id)
                .values(**values)
            )

        return await self.get(item_id)

    async def delete(self, item_id: str) -> bool:
        """Delete a memory item by ID."""
        async with self._engine.begin() as conn:
            result = await conn.execute(
                memory_items.delete().where(memory_items.c.id == item_id)
            )
            return result.rowcount > 0

    async def search(self, filter: MemoryFilter) -> list[MemoryItem]:
        """Search memory items using a MemoryFilter."""
        query = memory_items.select()

        if filter.agent_id:
            query = query.where(memory_items.c.agent_id == filter.agent_id)
        if filter.session_id:
            query = query.where(memory_items.c.session_id == filter.session_id)
        if filter.memory_type:
            query = query.where(memory_items.c.memory_type == filter.memory_type.value)
        if filter.scope:
            query = query.where(memory_items.c.scope == filter.scope.value)
        if filter.origin:
            query = query.where(memory_items.c.origin == filter.origin.value)
        if filter.keyword:
            # Escape SQL LIKE wildcards to prevent unintended pattern matching
            escaped = filter.keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            query = query.where(
                memory_items.c.content.ilike(f"%{escaped}%", escape="\\")
            )
        if filter.min_importance > 0:
            query = query.where(memory_items.c.importance >= filter.min_importance)
        if not filter.archived:
            query = query.where(memory_items.c.archived == False)  # noqa: E712

        query = query.order_by(memory_items.c.created_at.desc())

        async with self._engine.connect() as conn:
            result = await conn.execute(query)
            return [_row_to_memory_item(row) for row in result.fetchall()]

    async def list_by_scope(
        self,
        scope: MemoryScope,
        agent_id: str = "",
        limit: int = 100,
    ) -> list[MemoryItem]:
        """List memory items filtered by scope and optionally agent."""
        query = (
            memory_items.select()
            .where(memory_items.c.scope == scope.value)
            .where(memory_items.c.archived == False)  # noqa: E712
            .order_by(memory_items.c.created_at.desc())
            .limit(limit)
        )
        if agent_id:
            query = query.where(memory_items.c.agent_id == agent_id)

        async with self._engine.connect() as conn:
            result = await conn.execute(query)
            return [_row_to_memory_item(row) for row in result.fetchall()]

    # ── Memory Block Management ───────────────────────────────────

    async def create_block(
        self,
        agent_id: str,
        label: str,
        char_limit: int = 2000,
        initial_content: str = "",
    ) -> MemoryBlock:
        """Create a new memory block for an agent."""
        async with self._engine.begin() as conn:
            await conn.execute(
                memory_blocks.insert().values(
                    agent_id=agent_id,
                    label=label,
                    content=initial_content,
                    char_limit=char_limit,
                )
            )
        return MemoryBlock(
            label=label,
            content=initial_content,
            char_limit=char_limit,
            agent_id=agent_id,
        )

    async def get_block(self, agent_id: str, label: str) -> MemoryBlock | None:
        """Read a memory block by agent and label."""
        async with self._engine.connect() as conn:
            result = await conn.execute(
                memory_blocks.select().where(
                    sa.and_(
                        memory_blocks.c.agent_id == agent_id,
                        memory_blocks.c.label == label,
                    )
                )
            )
            row = result.fetchone()
            if row is None:
                return None
            return _row_to_block(row)

    async def update_block(self, agent_id: str, label: str, content: str) -> MemoryBlock | None:
        """Update a block's content. Validates char_limit."""
        block = await self.get_block(agent_id, label)
        if block is None:
            return None
        if len(content) > block.char_limit:
            raise ValueError(
                f"Content ({len(content)} chars) exceeds block limit "
                f"({block.char_limit} chars) for block {label!r}"
            )

        async with self._engine.begin() as conn:
            await conn.execute(
                memory_blocks.update()
                .where(
                    sa.and_(
                        memory_blocks.c.agent_id == agent_id,
                        memory_blocks.c.label == label,
                    )
                )
                .values(content=content)
            )
        block.content = content
        return block

    async def list_blocks(self, agent_id: str) -> list[MemoryBlock]:
        """List all blocks belonging to an agent."""
        async with self._engine.connect() as conn:
            result = await conn.execute(
                memory_blocks.select()
                .where(memory_blocks.c.agent_id == agent_id)
            )
            return [_row_to_block(row) for row in result.fetchall()]

    # ── Session Lifecycle ──────────────────────────────────────────

    async def create_session(self, session_id: str, agent_id: str) -> dict[str, Any]:
        """Create a new session state."""
        now = datetime.now(timezone.utc).isoformat()
        async with self._engine.begin() as conn:
            await conn.execute(
                sessions.insert().values(
                    id=session_id,
                    agent_id=agent_id,
                    status="active",
                    message_count=0,
                    created_at=now,
                )
            )
        return {
            "id": session_id,
            "agent_id": agent_id,
            "status": "active",
            "message_count": 0,
        }

    async def append_to_session(self, session_id: str, message: dict[str, Any]) -> None:
        """Increment session message count."""
        async with self._engine.begin() as conn:
            await conn.execute(
                sessions.update()
                .where(sessions.c.id == session_id)
                .values(message_count=sessions.c.message_count + 1)
            )

    async def get_session(self, session_id: str) -> dict[str, Any] | None:
        """Retrieve session state."""
        async with self._engine.connect() as conn:
            result = await conn.execute(
                sessions.select().where(sessions.c.id == session_id)
            )
            row = result.fetchone()
            if row is None:
                return None
            return {
                "id": row[sessions.c.id],
                "agent_id": row[sessions.c.agent_id],
                "status": row[sessions.c.status],
                "message_count": row[sessions.c.message_count],
            }

    async def get_recent(
        self,
        session_id: str,
        limit: int = 5,
    ) -> list[MemoryItem]:
        """Get the most recent memory items for a session."""
        query = (
            memory_items.select()
            .where(memory_items.c.session_id == session_id)
            .where(memory_items.c.archived == False)  # noqa: E712
            .order_by(memory_items.c.created_at.desc())
            .limit(limit)
        )
        async with self._engine.connect() as conn:
            result = await conn.execute(query)
            return [_row_to_memory_item(row) for row in result.fetchall()]

    async def archive_session(self, session_id: str) -> bool:
        """Archive a session and all its memories."""
        async with self._engine.begin() as conn:
            # Check session exists
            result = await conn.execute(
                sessions.select().where(sessions.c.id == session_id)
            )
            if result.fetchone() is None:
                return False

            await conn.execute(
                sessions.update()
                .where(sessions.c.id == session_id)
                .values(status="archived")
            )
            await conn.execute(
                memory_items.update()
                .where(memory_items.c.session_id == session_id)
                .values(archived=True)
            )
        return True

    async def destroy_session(self, session_id: str) -> bool:
        """Destroy a session and remove all its memory items."""
        async with self._engine.begin() as conn:
            result = await conn.execute(
                sessions.select().where(sessions.c.id == session_id)
            )
            if result.fetchone() is None:
                return False

            await conn.execute(
                memory_items.delete()
                .where(memory_items.c.session_id == session_id)
            )
            await conn.execute(
                sessions.delete().where(sessions.c.id == session_id)
            )
        return True

    # ── Agent CRUD ──────────────────────────────────────────────────

    async def store_agent(self, agent: dict[str, Any]) -> None:
        """Persist an agent record."""
        async with self._engine.begin() as conn:
            await conn.execute(
                agents.insert().values(
                    id=agent["id"],
                    name=agent.get("name", ""),
                    description=agent.get("description", ""),
                    status=agent.get("status", "idle"),
                    model=agent.get("model", "gpt-4o-mini"),
                    tools_json=json.dumps(agent.get("tools", [])),
                    created_at=agent.get("created_at", datetime.now(timezone.utc).isoformat()),
                    updated_at=agent.get("updated_at", datetime.now(timezone.utc).isoformat()),
                )
            )

    async def get_agent(self, agent_id: str) -> dict[str, Any] | None:
        """Retrieve an agent by ID."""
        async with self._engine.connect() as conn:
            result = await conn.execute(
                agents.select().where(agents.c.id == agent_id)
            )
            row = result.fetchone()
            if row is None:
                return None
            return _row_to_agent(row)

    async def list_agents(self) -> list[dict[str, Any]]:
        """List all agents."""
        async with self._engine.connect() as conn:
            result = await conn.execute(agents.select())
            return [_row_to_agent(row) for row in result.fetchall()]

    async def update_agent(self, agent_id: str, **kwargs: Any) -> bool:
        """Update an agent's fields."""
        values: dict[str, Any] = {
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        if "name" in kwargs:
            values["name"] = kwargs["name"]
        if "description" in kwargs:
            values["description"] = kwargs["description"]
        if "status" in kwargs:
            values["status"] = kwargs["status"]
        if "model" in kwargs:
            values["model"] = kwargs["model"]
        if "tools" in kwargs:
            values["tools_json"] = json.dumps(kwargs["tools"])

        async with self._engine.begin() as conn:
            result = await conn.execute(
                agents.update().where(agents.c.id == agent_id).values(**values)
            )
            return result.rowcount > 0

    async def delete_agent(self, agent_id: str) -> bool:
        """Delete an agent."""
        async with self._engine.begin() as conn:
            result = await conn.execute(
                agents.delete().where(agents.c.id == agent_id)
            )
            return result.rowcount > 0
