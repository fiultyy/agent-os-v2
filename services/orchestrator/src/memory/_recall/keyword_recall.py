"""Keyword-based recall strategy."""

from src.memory.store import InMemoryStore
from src.memory.types import MemoryFilter, MemoryItem, MemoryType, MemoryScope

from .base import RecallStrategy


class KeywordRecall(RecallStrategy):
    """Case-insensitive keyword matching recall."""

    def __init__(self, store: InMemoryStore) -> None:
        self._store = store

    async def recall(
        self,
        query: str,
        agent_id: str,
        session_id: str,
        memory_type: MemoryType | None,
        scope: MemoryScope | None,
        top_k: int,
    ) -> list[MemoryItem]:
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
