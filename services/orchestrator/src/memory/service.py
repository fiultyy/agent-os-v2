"""MemoryService — high-level facade for agent memory operations.

Provides the main API used by the orchestrator and graph nodes:
- :meth:`store` / :meth:`get` / :meth:`update` — CRUD on memory items.
- :meth:`recall` — keyword or semantic memory retrieval.
- Block management for persona and user profile.
- Session lifecycle management.
- Cross-agent shared memory with permission control.
- Access logging for audit trail.
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
from src.memory.permissions import (
    PermissionManager,
    PermissionLevel,
    ACTION_READ,
    ACTION_WRITE,
    ACTION_DELETE,
)


class MemoryService:
    """High-level memory management service for agents.

    Wraps an :class:`InMemoryStore` (and optional :class:`VectorStore`)
    and provides domain-level operations including keyword/semantic recall,
    block management, session lifecycle, and cross-agent permission-controlled
    access.

    Attributes:
        store: The underlying persistence layer.
        vector_store: Optional vector store for semantic retrieval.
        permissions: Permission manager for cross-agent access control.
    """

    def __init__(
        self,
        store: InMemoryStore | None = None,
        vector_store: VectorStore | None = None,
        permission_manager: PermissionManager | None = None,
    ) -> None:
        self._store = store or InMemoryStore()
        self._vector_store = vector_store
        self._permissions = permission_manager or PermissionManager()

    @property
    def store(self) -> InMemoryStore:
        """Access the underlying persistence layer."""
        return self._store

    @property
    def vector_store(self) -> VectorStore | None:
        """Access the optional vector store."""
        return self._vector_store

    @property
    def permissions(self) -> PermissionManager:
        """Access the permission manager."""
        return self._permissions

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

    async def get(
        self,
        memory_id: str,
        accessor_id: str = "",
    ) -> MemoryItem | None:
        """Retrieve a memory item by ID.

        If *accessor_id* is provided and differs from the item's owner,
        content is filtered by the effective permission level and the
        access is logged.

        Args:
            memory_id: ID of the memory to retrieve.
            accessor_id: Agent requesting access (for permission filtering).

        Returns:
            The memory item (possibly content-filtered), or None.
        """
        item = await self._store.get(memory_id)
        if item is None:
            return None

        if accessor_id and accessor_id != item.agent_id:
            # Apply permission filtering
            level = self._permissions.check_permission(
                accessor_id, item.agent_id, ACTION_READ,
            )
            self._permissions.log_access(
                accessor_id, item.agent_id, memory_id,
                ACTION_READ, level,
            )
            item.content = self._permissions.filter_content(
                accessor_id, item.agent_id, item.content,
            )

        return item

    async def update(
        self,
        memory_id: str,
        content: str | None = None,
        accessor_id: str = "",
        **kwargs: Any,
    ) -> MemoryItem | None:
        """Update a memory item's content and/or fields.

        Args:
            memory_id: ID of the item to update.
            content: New content text (optional).
            accessor_id: Agent performing the update (for permission check).
            **kwargs: Additional fields to update.

        Returns:
            The updated item, or None if not found or access denied.
        """
        item = await self._store.get(memory_id)
        if item is None:
            return None

        # Permission check for write access
        if accessor_id and accessor_id != item.agent_id:
            level = self._permissions.check_permission(
                accessor_id, item.agent_id, ACTION_WRITE,
            )
            self._permissions.log_access(
                accessor_id, item.agent_id, memory_id,
                ACTION_WRITE, level,
            )
            if level < PermissionLevel.ADMIN:
                return None  # Access denied

        item = await self._store.update(memory_id, content=content, **kwargs)
        # Re-index in vector store if content changed
        if item and content and self._vector_store is not None:
            await self._vector_store.add(memory_id, content)
        return item

    async def delete(
        self,
        memory_id: str,
        accessor_id: str = "",
    ) -> bool:
        """Delete a memory item.

        Args:
            memory_id: ID to delete.
            accessor_id: Agent performing the deletion (for permission check).

        Returns:
            True if deleted, False if not found or access denied.
        """
        if accessor_id:
            item = await self._store.get(memory_id)
            if item and accessor_id != item.agent_id:
                level = self._permissions.check_permission(
                    accessor_id, item.agent_id, ACTION_DELETE,
                )
                self._permissions.log_access(
                    accessor_id, item.agent_id, memory_id,
                    ACTION_DELETE, level,
                )
                if level < PermissionLevel.ADMIN:
                    return False

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
        include_shared: bool = False,
    ) -> list[MemoryItem]:
        """Recall memories matching a query.

        Supports two modes:
        - **KEYWORD**: Case-insensitive keyword matching (MVP default).
        - **SEMANTIC**: Vector similarity search + keyword rerank.

        When *include_shared* is True, also searches shared/workspace-scope
        memories and applies permission filtering to cross-agent results.

        Args:
            query: Search query text.
            agent_id: Optional agent filter.
            session_id: Optional session filter.
            memory_type: Optional memory tier filter.
            scope: Optional trust-domain filter.
            top_k: Maximum results to return.
            mode: Retrieval strategy.
            include_shared: Whether to include cross-agent shared memories.

        Returns:
            List of matching memory items (content may be filtered).
        """
        if mode == RecallMode.SEMANTIC and self._vector_store is not None:
            results = await self._recall_semantic(
                query, agent_id, session_id, memory_type, scope, top_k,
            )
        else:
            results = await self._recall_keyword(
                query, agent_id, session_id, memory_type, scope, top_k,
            )

        if include_shared and agent_id:
            shared = await self._recall_shared(query, agent_id, top_k)
            results.extend(shared)

        return results[:top_k]

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
        fetch_k = min(top_k * 3, 50)
        vector_results = await self._vector_store.search(query, top_k=fetch_k)

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
            item.metadata["_vector_score"] = score
            candidates.append(item)

        if query.strip():
            keywords = query.lower().split()
            for item in candidates:
                content_lower = item.content.lower()
                keyword_matches = sum(
                    1 for kw in keywords if kw in content_lower
                )
                vec_score = item.metadata.get("_vector_score", 0.0)
                keyword_ratio = keyword_matches / len(keywords) if keywords else 0
                item.metadata["_combined_score"] = (
                    0.7 * vec_score + 0.3 * keyword_ratio
                )
            candidates.sort(
                key=lambda i: i.metadata.get("_combined_score", 0.0),
                reverse=True,
            )

        return candidates[:top_k]

    async def _recall_shared(
        self,
        query: str,
        accessor_id: str,
        top_k: int,
    ) -> list[MemoryItem]:
        """Recall shared memories from other agents with permission filtering."""
        f = MemoryFilter(scope=MemoryScope.WORKSPACE)
        shared_items = await self._store.search(f)

        keywords = query.lower().split() if query.strip() else []
        results: list[MemoryItem] = []

        for item in shared_items:
            if item.agent_id == accessor_id:
                continue
            if item.archived:
                continue

            # Keyword match
            if keywords:
                content_lower = item.content.lower()
                if not any(kw in content_lower for kw in keywords):
                    continue

            # Apply permission filtering
            filtered_content = self._permissions.filter_content(
                accessor_id, item.agent_id, item.content,
            )
            item.content = filtered_content
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

    # ── Permission shortcuts ──────────────────────────────────────

    def grant_access(
        self,
        grantor_id: str,
        grantee_id: str,
        target_agent_id: str,
        level: int = 2,
        expires_at: str | None = None,
    ) -> str:
        """Grant memory access from one agent to another.

        Convenience wrapper around PermissionManager.grant.

        Args:
            grantor_id: Agent issuing the grant.
            grantee_id: Agent receiving access.
            target_agent_id: Agent whose memories are shared.
            level: Permission level (0-4).
            expires_at: Optional ISO-8601 expiry.

        Returns:
            Grant ID.
        """
        grant = self._permissions.grant(
            grantor_id=grantor_id,
            grantee_id=grantee_id,
            target_agent_id=target_agent_id,
            level=PermissionLevel(level),
            expires_at=expires_at,
        )
        return grant.id

    def get_access_log(self, **kwargs: Any) -> list:
        """Get the memory access log. See PermissionManager.get_access_log."""
        return self._permissions.get_access_log(**kwargs)
