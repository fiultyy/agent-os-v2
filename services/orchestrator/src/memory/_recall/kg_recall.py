"""Knowledge Graph recall strategy."""

from typing import Any

from src.memory.store import InMemoryStore
from src.memory.types import MemoryItem, MemoryType, MemoryScope

from .base import RecallStrategy


class KGRecall(RecallStrategy):
    """Recall memories via Knowledge Graph entity lookup.

    Extracts keywords from query, searches KG for matching entities,
    then loads their associated source_memory_ids from the store.
    """

    def __init__(self, store: InMemoryStore, knowledge_graph: Any) -> None:
        self._store = store
        self._kg = knowledge_graph

    async def recall(
        self,
        query: str,
        agent_id: str,
        session_id: str,
        memory_type: MemoryType | None,
        scope: MemoryScope | None,
        top_k: int,
    ) -> list[MemoryItem]:
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
