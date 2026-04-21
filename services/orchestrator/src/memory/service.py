"""MemoryService — thin facade that delegates to focused sub-components.

Provides the main API used by the orchestrator and graph nodes:
- :meth:`store` / :meth:`get` / :meth:`update` — CRUD on memory items.
- :meth:`recall` — keyword, semantic, or KG-based memory retrieval.
- Block management for persona and user profile.
- Session lifecycle management.
- Cross-agent shared memory with permission control.
- Access logging for audit trail.
"""

from typing import Any

from src.memory.types import (
    MemoryItem,
    MemoryRef,
    MemoryBlock,
    MemoryType,
    MemoryScope,
    RecallMode,
)
from src.memory.store import InMemoryStore
from src.memory.permissions import PermissionManager, PermissionLevel
from src.memory.scorer import ImportanceScorer
from src.memory._crud import CrudOperations
from src.memory._blocks import BlockOperations
from src.memory._session import SessionOperations
from src.memory._recall import (
    KeywordRecall,
    KGRecall,
    SharedRecall,
    UnifiedRecall,
)


class MemoryService:
    """High-level memory management service for agents.

    Wraps an :class:`InMemoryStore` (and optional :class:`KnowledgeGraph`)
    and provides domain-level operations including keyword/KG recall,
    block management, session lifecycle, and cross-agent permission-
    controlled access.

    This facade delegates to focused sub-components:
    - :class:`CrudOperations` for store/get/update/delete.
    - :class:`BlockOperations` for core memory block management.
    - :class:`SessionOperations` for session lifecycle and consolidation.
    - Individual recall strategy classes for each retrieval path.

    Attributes:
        store: The underlying persistence layer.
        kg: Optional knowledge graph for entity-based retrieval.
        permissions: Permission manager for cross-agent access control.
    """

    def __init__(
        self,
        store: InMemoryStore | None = None,
        knowledge_graph: Any | None = None,
        permission_manager: PermissionManager | None = None,
        auto_score: bool = True,
    ) -> None:
        self._store = store or InMemoryStore()
        self._kg = knowledge_graph
        self._permissions = permission_manager or PermissionManager()
        self._auto_score = auto_score
        self._scorer = ImportanceScorer() if auto_score else None

        # Sub-components
        self._crud = CrudOperations(
            store=self._store,
            vector_store=None,
            permissions=self._permissions,
            scorer=self._scorer,
            auto_score=self._auto_score,
        )
        self._blocks = BlockOperations(store=self._store)
        self._session = SessionOperations(
            store=self._store,
            blocks=self._blocks,
            store_func=self._crud.store,
        )

        # Recall strategies (semantic/vector removed)
        self._keyword_recall = KeywordRecall(store=self._store)
        self._kg_recall: KGRecall | None = None
        if knowledge_graph is not None:
            self._kg_recall = KGRecall(
                store=self._store, knowledge_graph=knowledge_graph,
            )
        self._shared_recall = SharedRecall(
            store=self._store, permissions=self._permissions,
        )

    @property
    def store_backend(self) -> InMemoryStore:
        """Access the underlying persistence layer."""
        return self._store

    @property
    def kg(self) -> Any | None:
        """Access the optional knowledge graph."""
        return self._kg

    @property
    def permissions(self) -> PermissionManager:
        """Access the permission manager."""
        return self._permissions

    # ── Memory Item Operations (delegates to CrudOperations) ────────

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
        return await self._crud.store(
            content=content,
            agent_id=agent_id,
            session_id=session_id,
            memory_type=memory_type,
            scope=scope,
            importance=importance,
            metadata=metadata,
        )

    async def get(
        self,
        memory_id: str,
        accessor_id: str = "",
    ) -> MemoryItem | None:
        """Retrieve a memory item by ID with optional permission filtering."""
        return await self._crud.get(memory_id=memory_id, accessor_id=accessor_id)

    async def update(
        self,
        memory_id: str,
        content: str | None = None,
        accessor_id: str = "",
        **kwargs: Any,
    ) -> MemoryItem | None:
        """Update memory item content/fields with permission check."""
        return await self._crud.update(
            memory_id=memory_id,
            content=content,
            accessor_id=accessor_id,
            **kwargs,
        )

    async def delete(
        self,
        memory_id: str,
        accessor_id: str = "",
    ) -> bool:
        """Delete a memory item with permission check."""
        return await self._crud.delete(memory_id=memory_id, accessor_id=accessor_id)

    # ── Recall (orchestrates recall strategies) ─────────────────────

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

        Supports two retrieval paths:
        - **KEYWORD**: Case-insensitive keyword matching.
        - **KG** (Knowledge Graph): Entity lookup -> associated memory IDs.

        When a KG is configured, both paths are combined.
        Results are deduplicated and KG-matched items receive a score boost.

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
        # Unified dual-path recall (P4)
        if (
            mode == RecallMode.UNIFIED
            or (mode == RecallMode.KEYWORD and self._kg_recall is not None)
        ) and self._kg_recall is not None:
            unified = UnifiedRecall(
                keyword_recall=self._keyword_recall,
                kg_recall=self._kg_recall,
            )
            results = await unified.recall(
                query, agent_id, session_id, memory_type, scope, top_k,
            )
        else:
            results = await self._keyword_recall.recall(
                query, agent_id, session_id, memory_type, scope, top_k,
            )

        if include_shared and agent_id:
            shared = await self._shared_recall.recall(
                query, agent_id, session_id, memory_type, scope, top_k,
            )
            results.extend(shared)

        return results[:top_k]

    # ── Block Management (delegates to BlockOperations) ─────────────

    async def init_agent_blocks(self, agent_id: str) -> None:
        """Initialize the default memory blocks for an agent.

        Creates ``persona`` and ``user_profile`` blocks with
        2000 character limits each.
        """
        await self._blocks.init_agent_blocks(agent_id)

    async def get_block(self, agent_id: str, label: str) -> MemoryBlock | None:
        """Read a memory block."""
        return await self._blocks.get_block(agent_id, label)

    async def update_block(self, agent_id: str, label: str, content: str) -> MemoryBlock | None:
        """Update a memory block's content.

        Raises:
            ValueError: If content exceeds the block's char_limit.
        """
        return await self._blocks.update_block(agent_id, label, content)

    async def list_blocks(self, agent_id: str) -> list[MemoryBlock]:
        """List all blocks for an agent."""
        return await self._blocks.list_blocks(agent_id)

    # ── Session Lifecycle (delegates to SessionOperations) ──────────

    async def create_session(self, session_id: str, agent_id: str) -> dict[str, Any]:
        """Create a new session and initialize agent blocks if needed."""
        return await self._session.create_session(session_id, agent_id)

    async def get_recent(self, session_id: str, limit: int = 5) -> list[MemoryItem]:
        """Get the most recent memory items for a session."""
        return await self._session.get_recent(session_id, limit=limit)

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

        Args:
            agent_id: The agent whose memories to consolidate.
            trigger: Reason for the consolidation.
            top_k: How many recent episodic items to consider.

        Returns:
            A list of :class:`MemoryRef` for the newly created semantic
            memories.  Empty if no consolidation was possible.
        """
        return await self._session.reflect(
            agent_id=agent_id,
            trigger=trigger,
            top_k=top_k,
            recall_func=self.recall,
        )

    async def archive_session(self, session_id: str) -> bool:
        """Archive a session and all its memories."""
        return await self._session.archive_session(session_id)

    async def destroy_session(self, session_id: str) -> bool:
        """Destroy a session and all its memory items."""
        return await self._session.destroy_session(session_id)

    # ── Permission shortcuts ────────────────────────────────────────

    def grant_access(
        self,
        grantor_id: str,
        grantee_id: str,
        target_agent_id: str,
        level: int = 2,
        expires_at: str | None = None,
    ) -> str:
        """Grant memory access from one agent to another."""
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
