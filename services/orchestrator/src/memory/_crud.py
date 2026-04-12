"""CRUD operations for memory items."""

import uuid
from typing import Any

from src.memory.store import InMemoryStore
from src.memory.vector import VectorStore
from src.memory.permissions import PermissionManager, PermissionLevel
from src.memory.permissions import ACTION_READ, ACTION_WRITE, ACTION_DELETE
from src.memory.scorer import ImportanceScorer
from src.memory.types import MemoryItem, MemoryRef, MemoryType, MemoryScope


class CrudOperations:
    """Handles store/get/update/delete for memory items."""

    def __init__(
        self,
        store: InMemoryStore,
        vector_store: VectorStore | None,
        permissions: PermissionManager,
        scorer: ImportanceScorer | None,
        auto_score: bool,
    ) -> None:
        self._store = store
        self._vector_store = vector_store
        self._permissions = permissions
        self._scorer = scorer
        self._auto_score = auto_score

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
