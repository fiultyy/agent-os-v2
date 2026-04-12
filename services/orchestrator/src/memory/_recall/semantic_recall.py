"""Semantic (vector similarity) recall strategy."""

from src.memory.store import InMemoryStore
from src.memory.vector import VectorStore
from src.memory.types import MemoryItem, MemoryType, MemoryScope

from .base import RecallStrategy


class SemanticRecall(RecallStrategy):
    """Vector search top-k then keyword rerank."""

    def __init__(self, store: InMemoryStore, vector_store: VectorStore) -> None:
        self._store = store
        self._vector_store = vector_store

    async def recall(
        self,
        query: str,
        agent_id: str,
        session_id: str,
        memory_type: MemoryType | None,
        scope: MemoryScope | None,
        top_k: int,
    ) -> list[MemoryItem]:
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
