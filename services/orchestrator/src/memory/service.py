"""MemoryService — high-level facade for agent memory operations.

Provides the main API used by the orchestrator and graph nodes:
- :meth:`store` / :meth:`get` / :meth:`update` — CRUD on memory items.
- :meth:`recall` — keyword-based memory retrieval (MVP).
- Block management for persona and user profile.
- Session lifecycle management.
"""

import uuid
from typing import Any

from src.memory.types import (
    MemoryItem,
    MemoryRef,
    MemoryBlock,
    MemoryFilter,
    MemoryType,
    MemoryScope,
)
from src.memory.store import InMemoryStore


class MemoryService:
    """High-level memory management service for agents.

    Wraps an :class:`InMemoryStore` and provides domain-level
    operations including keyword-based recall, block management,
    and session lifecycle.

    Attributes:
        store: The underlying persistence layer.
    """

    def __init__(self, store: InMemoryStore | None = None) -> None:
        self._store = store or InMemoryStore()

    @property
    def store(self) -> InMemoryStore:
        """Access the underlying persistence layer."""
        return self._store

    # ── Memory Item Operations ────────────────────────────────────

    async def store(
        self,
        content: str,
        agent_id: str = "",
        session_id: str = "",
        memory_type: MemoryType = MemoryType.SESSION,
        scope: MemoryScope = MemoryScope.AGENT,
        importance: float = 0.5,
        metadata: dict[str, Any] | None = None,
    ) -> MemoryRef:
        """Store a new memory item and return a reference.

        Args:
            content: The memory content text.
            agent_id: Owning agent ID.
            session_id: Session ID (for session-level memories).
            memory_type: Memory tier.
            scope: Trust-domain scope.
            importance: Importance score (0.0–1.0).
            metadata: Optional key-value metadata.

        Returns:
            A :class:`MemoryRef` pointing to the stored item.
        """
        item = MemoryItem(
            id=str(uuid.uuid4()),
            agent_id=agent_id,
            session_id=session_id,
            memory_type=memory_type,
            scope=scope,
            content=content,
            importance=importance,
            metadata=metadata or {},
        )
        item_id = await self._store.store(item)
        return MemoryRef(id=item_id, memory_type=memory_type, scope=scope)

    async def get(self, memory_id: str) -> MemoryItem | None:
        """Retrieve a memory item by ID."""
        return await self._store.get(memory_id)

    async def update(
        self,
        memory_id: str,
        content: str | None = None,
        **kwargs: Any,
    ) -> MemoryItem | None:
        """Update a memory item's content and/or fields."""
        return await self._store.update(memory_id, content=content, **kwargs)

    async def delete(self, memory_id: str) -> bool:
        """Delete a memory item."""
        return await self._store.delete(memory_id)

    # ── Recall (keyword matching) ──────────────────────────────────

    async def recall(
        self,
        query: str,
        agent_id: str = "",
        session_id: str = "",
        memory_type: MemoryType | None = None,
        scope: MemoryScope | None = None,
        top_k: int = 10,
    ) -> list[MemoryItem]:
        """Recall memories matching a keyword query.

        MVP implementation uses simple case-insensitive keyword
        matching against memory content. Returns items sorted by
        creation time (newest first).

        Args:
            query: Keyword(s) to search for.
            agent_id: Optional agent filter.
            session_id: Optional session filter.
            memory_type: Optional memory tier filter.
            scope: Optional trust-domain filter.
            top_k: Maximum results to return.

        Returns:
            List of matching memory items.
        """
        f = MemoryFilter(
            agent_id=agent_id,
            session_id=session_id,
            memory_type=memory_type,
            scope=scope,
        )

        all_items = await self._store.search(f)

        # Empty query returns all items (limited by top_k)
        if not query.strip():
            return all_items[:top_k]

        keywords = query.lower().split()
        results = []
        for item in all_items:
            content_lower = item.content.lower()
            if any(kw in content_lower for kw in keywords):
                results.append(item)
                if len(results) >= top_k:
                    break

        return results

    # ── Block Management ───────────────────────────────────────────

    async def init_agent_blocks(self, agent_id: str) -> None:
        """Initialize the default memory blocks for an agent.

        Creates ``persona`` and ``user_profile`` blocks with
        2000 character limits each.
        """
        await self._store.create_block(
            agent_id, "persona", char_limit=2000, initial_content=""
        )
        await self._store.create_block(
            agent_id, "user_profile", char_limit=2000, initial_content=""
        )

    async def get_block(self, agent_id: str, label: str) -> MemoryBlock | None:
        """Read a memory block."""
        return await self._store.get_block(agent_id, label)

    async def update_block(self, agent_id: str, label: str, content: str) -> MemoryBlock | None:
        """Update a memory block's content.

        Raises:
            ValueError: If content exceeds the block's char_limit.
        """
        return await self._store.update_block(agent_id, label, content)

    async def list_blocks(self, agent_id: str) -> list[MemoryBlock]:
        """List all blocks for an agent."""
        return await self._store.list_blocks(agent_id)

    # ── Session Lifecycle ──────────────────────────────────────────

    async def create_session(self, session_id: str, agent_id: str) -> dict[str, Any]:
        """Create a new session and initialize agent blocks if needed."""
        blocks = await self._store.list_blocks(agent_id)
        if not blocks:
            await self.init_agent_blocks(agent_id)
        return await self._store.create_session(session_id, agent_id)

    async def get_recent(self, session_id: str, limit: int = 5) -> list[MemoryItem]:
        """Get the most recent memory items for a session."""
        return await self._store.get_recent(session_id, limit=limit)

    async def archive_session(self, session_id: str) -> bool:
        """Archive a session and all its memories."""
        return await self._store.archive_session(session_id)

    async def destroy_session(self, session_id: str) -> bool:
        """Destroy a session and all its memory items."""
        return await self._store.destroy_session(session_id)
