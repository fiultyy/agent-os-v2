"""MemoryService — high-level facade for agent memory operations.

Provides the main API used by the orchestrator and graph nodes:
- :meth:`store` / :meth:`get` / :meth:`update` — CRUD on memory items.
- :meth:`recall` — keyword or semantic memory retrieval.
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
    RecallMode,
)
from src.memory.store import InMemoryStore
from src.memory.vector import VectorStore


class MemoryService:
    """High-level memory management service for agents.

    Wraps an :class:`InMemoryStore` (and optional :class:`VectorStore`)
    and provides domain-level operations including keyword/semantic recall,
    block management, and session lifecycle.

    Attributes:
        store: The underlying persistence layer.
        vector_store: Optional vector store for semantic retrieval.
    """

    def __init__(
        self,
        store: InMemoryStore | None = None,
        vector_store: VectorStore | None = None,
    ) -> None:
        self._store = store or InMemoryStore()
        self._vector_store = vector_store

    @property
    def store(self) -> InMemoryStore:
        """Access the underlying persistence layer."""
        return self._store

    @property
    def vector_store(self) -> VectorStore | None:
        """Access the optional vector store."""
        return self._vector_store

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

        # Index in vector store for semantic search
        if self._vector_store is not None:
            await self._vector_store.add(item_id, content)

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
        item = await self._store.update(memory_id, content=content, **kwargs)
        # Re-index in vector store if content changed
        if item and content and self._vector_store is not None:
            await self._vector_store.add(memory_id, content)
        return item

    async def delete(self, memory_id: str) -> bool:
        """Delete a memory item."""
        if self._vector_store is not None:
            await self._vector_store.delete(memory_id)
        return await self._store.delete(memory_id)

    # ── Recall ──────────────────────────────────────────────────────

    async def recall(
        self,
        query: str,
        agent_id: str = "",
        session_id: str = "",
        memory_type: MemoryType | None = None,
        scope: MemoryScope | None = None,
        top_k: int = 10,
        mode: RecallMode = RecallMode.KEYWORD,
    ) -> list[MemoryItem]:
        """Recall memories matching a query.

        Supports two modes:
        - **KEYWORD**: Case-insensitive keyword matching (MVP default).
        - **SEMANTIC**: Vector similarity search + keyword rerank.

        Args:
            query: Search query text.
            agent_id: Optional agent filter.
            session_id: Optional session filter.
            memory_type: Optional memory tier filter.
            scope: Optional trust-domain filter.
            top_k: Maximum results to return.
            mode: Retrieval strategy.

        Returns:
            List of matching memory items.
        """
        if mode == RecallMode.SEMANTIC and self._vector_store is not None:
            return await self._recall_semantic(
                query, agent_id, session_id, memory_type, scope, top_k,
            )
        return await self._recall_keyword(
            query, agent_id, session_id, memory_type, scope, top_k,
        )

    async def _recall_keyword(
        self,
        query: str,
        agent_id: str,
        session_id: str,
        memory_type: MemoryType | None,
        scope: MemoryScope | None,
        top_k: int,
    ) -> list[MemoryItem]:
        """Keyword-based recall (original MVP implementation)."""
        f = MemoryFilter(
            agent_id=agent_id,
            session_id=session_id,
            memory_type=memory_type,
            scope=scope,
        )

        all_items = await self._store.search(f)

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

    async def _recall_semantic(
        self,
        query: str,
        agent_id: str,
        session_id: str,
        memory_type: MemoryType | None,
        scope: MemoryScope | None,
        top_k: int,
    ) -> list[MemoryItem]:
        """Semantic recall: vector search top-k, then keyword rerank."""
        # Over-fetch to allow room for filtering
        fetch_k = min(top_k * 3, 50)
        vector_results = await self._vector_store.search(query, top_k=fetch_k)

        # Load items and apply structural filters
        candidates: list[MemoryItem] = []
        for mid, score in vector_results:
            item = await self._store.get(mid)
            if item is None or item.archived:
                continue
            if agent_id and item.agent_id != agent_id:
                continue
            if session_id and item.session_id != session_id:
                continue
            if memory_type and item.memory_type != memory_type:
                continue
            if scope and item.scope != scope:
                continue
            # Store vector score in metadata for downstream use
            item.metadata["_vector_score"] = score
            candidates.append(item)

        # Rerank: boost items that also match keywords
        if query.strip():
            keywords = query.lower().split()
            for item in candidates:
                content_lower = item.content.lower()
                keyword_matches = sum(
                    1 for kw in keywords if kw in content_lower
                )
                vec_score = item.metadata.get("_vector_score", 0.0)
                # Combine: 70% vector + 30% keyword match ratio
                keyword_ratio = keyword_matches / len(keywords) if keywords else 0
                item.metadata["_combined_score"] = (
                    0.7 * vec_score + 0.3 * keyword_ratio
                )
            candidates.sort(
                key=lambda i: i.metadata.get("_combined_score", 0.0),
                reverse=True,
            )

        return candidates[:top_k]

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
