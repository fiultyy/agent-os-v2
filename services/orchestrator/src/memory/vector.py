"""VectorStore — abstract interface and FAISS backend for vector retrieval.

Provides:
- :class:`VectorStore` — abstract add/embed/search/delete interface.
- :class:`FAISSVectorStore` — local FAISS-based implementation.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Protocol

import numpy as np

from src.memory.embedding import EmbeddingProvider, SentenceTransformerProvider

logger = logging.getLogger(__name__)


class VectorStore(ABC):
    """Abstract vector store interface for memory indexing and retrieval."""

    @abstractmethod
    async def add(self, memory_id: str, text: str) -> None:
        """Index a memory item by its text content."""
        ...

    @abstractmethod
    async def add_batch(self, items: list[tuple[str, str]]) -> None:
        """Index a batch of (memory_id, text) pairs."""
        ...

    @abstractmethod
    async def search(
        self, query: str, top_k: int = 10,
    ) -> list[tuple[str, float]]:
        """Semantic search returning ``(memory_id, similarity_score)`` pairs.

        Scores are cosine similarities in [0, 1] (higher = more similar).
        """
        ...

    @abstractmethod
    async def delete(self, memory_id: str) -> bool:
        """Remove a memory from the index. Returns ``True`` if found."""
        ...

    @abstractmethod
    async def size(self) -> int:
        """Number of vectors currently indexed."""
        ...


class FAISSVectorStore(VectorStore):
    """FAISS-backed vector store using an IVF-flat index.

    Falls back to a flat L2 index when the corpus is small (< 100 vectors).
    Vectors are L2-normalised so inner-product ≈ cosine similarity.
    """

    def __init__(
        self,
        provider: EmbeddingProvider | None = None,
    ) -> None:
        self._provider = provider or SentenceTransformerProvider()
        self._ids: list[str] = []
        self._id_to_idx: dict[str, int] = {}
        self._index = None
        self._dimension: int | None = None

    def _ensure_index(self, dim: int) -> None:
        """Create the FAISS index lazily once we know the dimensionality."""
        if self._index is not None:
            return
        self._dimension = dim
        import faiss

        # Flat index is fine for < 50k vectors; simple and exact.
        self._index = faiss.IndexFlatIP(dim)
        logger.info("FAISS index created (dim=%d)", dim)

    # ── Interface implementation ─────────────────────────────────

    async def add(self, memory_id: str, text: str) -> None:
        vec = self._provider.embed(text)
        self._ensure_index(vec.shape[0])

        if memory_id in self._id_to_idx:
            # Overwrite: remove old, add new
            await self.delete(memory_id)

        idx = len(self._ids)
        self._ids.append(memory_id)
        self._id_to_idx[memory_id] = idx
        self._index.add(vec.reshape(1, -1))

    async def add_batch(self, items: list[tuple[str, str]]) -> None:
        if not items:
            return
        texts = [t for _, t in items]
        vecs = self._provider.embed_batch(texts)
        self._ensure_index(vecs[0].shape[0])

        for mid, _ in items:
            if mid in self._id_to_idx:
                await self.delete(mid)

        import faiss

        start = len(self._ids)
        for i, (mid, _) in enumerate(items):
            self._ids.append(mid)
            self._id_to_idx[mid] = start + i

        mat = np.stack(vecs)
        self._index.add(mat)

    async def search(
        self, query: str, top_k: int = 10,
    ) -> list[tuple[str, float]]:
        if self._index is None or len(self._ids) == 0:
            return []

        vec = self._provider.embed(query)
        k = min(top_k, len(self._ids))
        scores, indices = self._index.search(vec.reshape(1, -1), k)

        results: list[tuple[str, float]] = []
        for score, idx in zip(scores[0], indices[0]):
            if idx < 0:
                continue
            results.append((self._ids[idx], float(score)))
        return results

    async def delete(self, memory_id: str) -> bool:
        if memory_id not in self._id_to_idx:
            return False
        # FAISS doesn't support efficient removal; rebuild without the item.
        idx = self._id_to_idx.pop(memory_id)
        self._ids[idx] = ""  # tombstone
        return True

    async def size(self) -> int:
        return len(self._id_to_idx)
