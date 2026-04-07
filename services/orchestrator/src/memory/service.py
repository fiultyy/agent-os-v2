"""MemoryService — high-level facade for agent memory operations.

Provides the main API used by the orchestrator and graph nodes:
- :meth:`store` / :meth:`get` / :meth:`update` — CRUD on memory items.
- :meth:`recall` — keyword, semantic, or KG-based memory retrieval.
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
from src.memory.scorer import ImportanceScorer


class MemoryService:
    """High-level memory management service for agents.

    Wraps an :class:`InMemoryStore` (and optional :class:`VectorStore`
    and :class:`KnowledgeGraph`) and provides domain-level operations
    including keyword/semantic/KG recall, block management, session
    lifecycle, and and cross-agent permission-controlled access.

    Attributes:
        store: The underlying persistence layer.
        vector_store: Optional vector store for semantic retrieval.
        kg: Optional knowledge graph for entity-based retrieval.
        permissions: Permission manager for cross-agent access control.
    """

    def __init__(
        self,
        store: InMemoryStore | None = None,
        vector_store: VectorStore | None = None,
        knowledge_graph: Any | None = None,
        permission_manager: PermissionManager | None = None,
        auto_score: bool = True,
    ) -> None:
        self._store = store or InMemoryStore()
        self._vector_store = vector_store
        self._kg = knowledge_graph
        self._permissions = permission_manager or PermissionManager()
        self._auto_score = auto_score
        self._scorer = ImportanceScorer() if auto_score else None

    @property
    def store(self) -> InMemoryStore:
        """Access the underlying persistence layer."""
        return self._store

    @property
    def vector_store(self) -> VectorStore | None:
        """Access the optional vector store."""
        return self._vector_store

    @property
    def kg(self) -> Any | None:
        """Access the optional knowledge graph."""
        return self._kg

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
        """Store a new memory item and return a reference."""
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

        if self._auto_score and importance == 0.5 and self._scorer is not None:
            scored = self._scorer.score(item)
            item.importance = scored.total

        item_id = await self._store.store(item)

        if self._vector_store is not None:
            await self._vector_store.add(item_id, content)

        return MemoryRef(id=item_id, memory_type=memory_type, scope=scope)

    async def get(
        self,
        memory_id: str,
        accessor_id: str = "",
    ) -> MemoryItem | None:
        """Retrieve a memory item by ID with optional permission filtering."""
        item = await self._store.get(memory_id)
        if item is None:
            return None

        if accessor_id and accessor_id != item.agent_id:
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
        """Update memory item content/fields with permission check."""
        item = await self._store.get(memory_id)
        if item is None:
            return None

        if accessor_id and accessor_id != item.agent_id:
            level = self._permissions.check_permission(
                accessor_id, item.agent_id, ACTION_WRITE,
            )
            self._permissions.log_access(
                accessor_id, item.agent_id, memory_id,
                ACTION_WRITE, level,
            )
            if level < PermissionLevel.ADMIN:
                return None

        item = await self._store.update(memory_id, content=content, **kwargs)
        if item and content and self._vector_store is not None:
            await self._vector_store.add(memory_id, content)
        return item

    async def delete(
        self,
        memory_id: str,
        accessor_id: str = "",
    ) -> bool:
        """Delete a memory item with permission check."""
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

        Supports three retrieval paths:
        - **KEYWORD**: Case-insensitive keyword matching.
        - **SEMANTIC**: Vector similarity search + keyword rerank.
        - **KG** (Knowledge Graph): Entity lookup -> associated memory IDs.

        When mode=SEMANTIC and a KG is configured, all three paths
        are combined. Results are deduplicated and KG-matched items
        receive a score boost.

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

        # Third retrieval path — Knowledge Graph
        if self._kg is not None and query.strip():
            kg_results = await self._recall_kg(
                query, agent_id, session_id, memory_type, scope,
            )
            if kg_results:
                seen_ids: set[str] = {item.id for item in results}
                for item in kg_results:
                    if item.id not in seen_ids:
                        item.metadata["_kg_match"] = True
                        results.append(item)
                        seen_ids.add(item.id)
                results.sort(
                    key=lambda i: (
                        1.0 if i.metadata.get("_kg_match") else 0.0,
                        i.importance,
                    ),
                    reverse=True,
                )

        if include_shared and agent_id:
            shared = await self._recall_shared(query, agent_id, top_k)
            results.extend(shared)

        return results[:top_k]

    async def _recall_kg(
        self,
        query: str,
        agent_id: str,
        session_id: str,
        memory_type: MemoryType | None,
        scope: MemoryScope | None,
    ) -> list[MemoryItem]:
        """Recall memories via Knowledge Graph entity lookup.

        Extracts keywords from query, searches KG for matching entities,
        then loads their associated source_memory_ids from the store.
        """
        if self._kg is None:
            return []

        results: list[MemoryItem] = []
        seen: set[str] = set()
        keywords = query.split()

        for kw in keywords:
            if len(kw) < 2:
                continue
            try:
                entities = self._kg.search_entities(kw, limit=5)
            except Exception:
                continue
            for ent in entities:
                source_ids: list[str] = ent.get("source_memory_ids", [])
                for mid in source_ids:
                    if mid in seen:
                        continue
                    seen.add(mid)
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
                    results.append(item)

        return results

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

    # ── Memory Consolidation ───────────────────────────────────────

    async def reflect(
        self,
        agent_id: str,
        trigger: str = "periodic",
        top_k: int = 20,
    ) -> list[MemoryRef]:
        """Consolidate recent episodic memories into semantic knowledge.

        Retrieves the most recent EPISODIC memories for *agent_id*,
        inspects them for duplicates and contradictions, then merges
        related items into new SEMANTIC memories.

        This implements the "L2 → L3" migration described in the
        architecture doc (D-07): episodic experiences are periodically
        distilled into durable semantic knowledge.

        Args:
            agent_id: The agent whose memories to consolidate.
            trigger: Reason for the consolidation (e.g. ``"periodic"``,
                ``"session_end"``).  Stored in the new semantic memory's
                metadata for auditability.
            top_k: How many recent episodic items to consider.

        Returns:
            A list of :class:`MemoryRef` for the newly created semantic
            memories.  Empty if no consolidation was possible.
        """
        # 1. Recall recent episodic memories.
        episodic_items: list[MemoryItem] = await self.recall(
            query="",
            agent_id=agent_id,
            memory_type=MemoryType.EPISODIC,
            top_k=top_k,
        )

        if not episodic_items:
            return []

        # 2. Group by content similarity (simple keyword-overlap heuristic).
        groups: list[list[MemoryItem]] = []
        used_ids: set[str] = set()

        for item in episodic_items:
            if item.id in used_ids:
                continue
            group = [item]
            used_ids.add(item.id)
            words = set(item.content.lower().split())

            for other in episodic_items:
                if other.id in used_ids:
                    continue
                other_words = set(other.content.lower().split())
                # Jaccard-like overlap threshold (≥ 40 % shared tokens).
                if words and other_words:
                    overlap = len(words & other_words) / len(words | other_words)
                    if overlap >= 0.4:
                        group.append(other)
                        used_ids.add(other.id)

            groups.append(group)

        # 3. Merge each group into a single semantic memory.
        new_refs: list[MemoryRef] = []

        for group in groups:
            if len(group) < 2:
                # Singletons are not consolidated.
                continue

            # Simple merge strategy: concatenate unique content fragments.
            seen_fragments: set[str] = set()
            merged_parts: list[str] = []
            for member in group:
                fragment = member.content.strip()
                if fragment not in seen_fragments:
                    seen_fragments.add(fragment)
                    merged_parts.append(fragment)

            merged_content = "\n---\n".join(merged_parts)

            source_ids = [m.id for m in group]
            max_importance = max(m.importance for m in group)

            ref = await self.store(
                content=merged_content,
                agent_id=agent_id,
                memory_type=MemoryType.SEMANTIC,
                importance=max_importance,
                metadata={
                    "consolidation_trigger": trigger,
                    "source_ids": source_ids,
                    "merged_count": len(group),
                },
            )
            new_refs.append(ref)

        return new_refs

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
