"""Memory store — in-memory persistence layer for agent memories.

Provides :class:`InMemoryStore` for development use with full CRUD
operations, block management, and session lifecycle tracking.
"""

import uuid
from typing import Any

from src.memory.types import (
    MemoryItem,
    MemoryBlock,
    MemoryFilter,
    MemoryType,
    MemoryScope,
)


class InMemoryStore:
    """In-memory store for agent memories and blocks.

    All data is kept in process memory. Suitable for development
    and testing; replace with SQLite/Postgres for production.

    Attributes:
        _items: Memory items indexed by ID.
        _blocks: Memory blocks indexed by ``(agent_id, label)``.
        _sessions: Session states indexed by session ID.
    """

    def __init__(self) -> None:
        self._items: dict[str, MemoryItem] = {}
        self._blocks: dict[str, MemoryBlock] = {}  # key = "agent_id:label"
        self._sessions: dict[str, dict[str, Any]] = {}

    # ── Memory Item CRUD ──────────────────────────────────────────

    async def store(self, item: MemoryItem) -> str:
        """Store a memory item. Assigns an ID if none provided.

        Args:
            item: The memory item to persist.

        Returns:
            The item's unique ID.
        """
        if not item.id:
            item.id = str(uuid.uuid4())
        self._items[item.id] = item
        return item.id

    async def get(self, item_id: str) -> MemoryItem | None:
        """Retrieve a memory item by ID, or ``None`` if not found."""
        item = self._items.get(item_id)
        if item is not None:
            item.touch()
        return item

    async def update(self, item_id: str, content: str | None = None, **kwargs: Any) -> MemoryItem | None:
        """Update a memory item's content and/or fields.

        Args:
            item_id: ID of the item to update.
            content: New content text (optional).
            **kwargs: Additional fields to update (e.g. importance, metadata).

        Returns:
            The updated item, or ``None`` if not found.
        """
        item = self._items.get(item_id)
        if item is None:
            return None

        if content is not None:
            item.content = content
        for key, value in kwargs.items():
            if hasattr(item, key):
                setattr(item, key, value)
        item.touch()
        return item

    async def delete(self, item_id: str) -> bool:
        """Delete a memory item by ID.

        Returns:
            ``True`` if the item existed and was deleted.
        """
        return self._items.pop(item_id, None) is not None

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
        results = []
        for item in self._items.values():
            if item.scope != scope:
                continue
            if agent_id and item.agent_id != agent_id:
                continue
            if item.archived:
                continue
            results.append(item)
            if len(results) >= limit:
                break
        return results

    async def search(self, filter: MemoryFilter) -> list[MemoryItem]:
        """Search memory items using a :class:`MemoryFilter`.

        Returns all items matching every non-empty filter criterion.
        Results are sorted by creation time (newest first).
        """
        results = []
        for item in self._items.values():
            if not self._matches_filter(item, filter):
                continue
            results.append(item)

        results.sort(key=lambda i: i.created_at, reverse=True)
        return results

    def _matches_filter(self, item: MemoryItem, f: MemoryFilter) -> bool:
        """Check if *item* matches all non-empty filter criteria."""
        if f.agent_id and item.agent_id != f.agent_id:
            return False
        if f.session_id and item.session_id != f.session_id:
            return False
        if f.memory_type and item.memory_type != f.memory_type:
            return False
        if f.scope and item.scope != f.scope:
            return False
        if f.origin and item.origin != f.origin:
            return False
        if f.keyword and f.keyword.lower() not in item.content.lower():
            return False
        if f.min_importance and item.importance < f.min_importance:
            return False
        if not f.archived and item.archived:
            return False
        return True

    # ── Memory Block Management ───────────────────────────────────

    def _block_key(self, agent_id: str, label: str) -> str:
        return f"{agent_id}:{label}"

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
            label: Block label (e.g. ``"persona"``, ``"user_profile"``).
            char_limit: Maximum character count.
            initial_content: Optional starting content.

        Returns:
            The newly created block.
        """
        key = self._block_key(agent_id, label)
        block = MemoryBlock(
            label=label,
            content=initial_content,
            char_limit=char_limit,
            agent_id=agent_id,
        )
        self._blocks[key] = block
        return block

    async def get_block(self, agent_id: str, label: str) -> MemoryBlock | None:
        """Read a memory block by agent and label."""
        return self._blocks.get(self._block_key(agent_id, label))

    async def update_block(self, agent_id: str, label: str, content: str) -> MemoryBlock | None:
        """Update a block's content. Validates char_limit.

        Returns:
            The updated block, or ``None`` if not found.

        Raises:
            ValueError: If content exceeds the block's char_limit.
        """
        block = self._blocks.get(self._block_key(agent_id, label))
        if block is None:
            return None
        block.write(content)
        return block

    async def list_blocks(self, agent_id: str) -> list[MemoryBlock]:
        """List all blocks belonging to an agent."""
        prefix = f"{agent_id}:"
        return [b for k, b in self._blocks.items() if k.startswith(prefix)]

    # ── Session Lifecycle ──────────────────────────────────────────

    async def create_session(self, session_id: str, agent_id: str) -> dict[str, Any]:
        """Create a new session state.

        Args:
            session_id: Unique session identifier.
            agent_id: Agent participating in this session.

        Returns:
            The session state dictionary.
        """
        session = {
            "id": session_id,
            "agent_id": agent_id,
            "status": "active",
            "message_count": 0,
        }
        self._sessions[session_id] = session
        return session

    async def append_to_session(self, session_id: str, message: dict[str, Any]) -> None:
        """Append a message to the session and increment counter."""
        session = self._sessions.get(session_id)
        if session is None:
            return
        session["message_count"] = session.get("message_count", 0) + 1

    async def get_session(self, session_id: str) -> dict[str, Any] | None:
        """Retrieve session state."""
        return self._sessions.get(session_id)

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
        items = [
            item for item in self._items.values()
            if item.session_id == session_id and not item.archived
        ]
        items.sort(key=lambda i: i.created_at, reverse=True)
        return items[:limit]

    async def archive_session(self, session_id: str) -> bool:
        """Archive a session: mark status and archive all its memories.

        Returns:
            ``True`` if the session existed and was archived.
        """
        session = self._sessions.get(session_id)
        if session is None:
            return False

        session["status"] = "archived"

        for item in self._items.values():
            if item.session_id == session_id:
                item.archived = True

        return True

    async def destroy_session(self, session_id: str) -> bool:
        """Destroy a session and remove all its memory items.

        Returns:
            ``True`` if the session existed.
        """
        if session_id not in self._sessions:
            return False

        del self._sessions[session_id]

        ids_to_remove = [
            item_id for item_id, item in self._items.items()
            if item.session_id == session_id
        ]
        for item_id in ids_to_remove:
            del self._items[item_id]

        return True
