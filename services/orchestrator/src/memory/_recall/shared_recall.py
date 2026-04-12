"""Shared-scope cross-agent recall strategy."""

from src.memory.store import InMemoryStore
from src.memory.permissions import PermissionManager
from src.memory.types import MemoryItem, MemoryScope, MemoryFilter, MemoryType

from .base import RecallStrategy


class SharedRecall(RecallStrategy):
    """Recall shared memories from other agents with permission filtering."""

    def __init__(
        self,
        store: InMemoryStore,
        permissions: PermissionManager,
    ) -> None:
        self._store = store
        self._permissions = permissions

    async def recall(
        self,
        query: str,
        agent_id: str,
        session_id: str,
        memory_type: MemoryType | None,
        scope: MemoryScope | None,
        top_k: int,
    ) -> list[MemoryItem]:
        f = MemoryFilter(scope=MemoryScope.WORKSPACE)
        shared_items = await self._store.search(f)

        keywords = query.lower().split() if query.strip() else []
        results: list[MemoryItem] = []

        for item in shared_items:
            if item.agent_id == agent_id:
                continue
            if item.archived:
                continue

            if keywords:
                content_lower = item.content.lower()
                if not any(kw in content_lower for kw in keywords):
                    continue

            filtered_content = self._permissions.filter_content(
                agent_id, item.agent_id, item.content,
            )
            item.content = filtered_content
            results.append(item)
            if len(results) >= top_k:
                break

        return results
