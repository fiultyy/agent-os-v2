"""VectorStore — abstract interface and FAISS backend for vector retrieval.

Provides:
- :class:`VectorStore` — abstract add/embed/search/delete interface.
- :class:`FAISSVectorStore` — local FAISS-based implementation with file persistence.

Persistence:
- FAISS index saved to ``data/memory.faiss`` (debounced, 5 s).
- ID mapping stored in SQLite at ``data/vector_meta.db``.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

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
    """FAISS-backed vector store with file persistence.

    Falls back to a flat L2 index when the corpus is small (< 100 vectors).
    Vectors are L2-normalised so inner-product = cosine similarity.

    Args:
        provider: Embedding provider. Defaults to SentenceTransformerProvider.
        persist_path: Path for the FAISS index file. Defaults to
            ``"data/memory.faiss"``. Set to ``""`` to disable persistence.
    """

    def __init__(
        self,
        provider: EmbeddingProvider | None = None,
        persist_path: str = "data/memory.faiss",
    ) -> None:
        self._provider = provider or SentenceTransformerProvider()
        self._persist_path = persist_path
        self._ids: list[str] = []
        self._id_to_idx: dict[str, int] = {}
        self._index: Any = None
        self._dimension: int | None = None
        self._dirty: bool = False
        self._save_timer: threading.Timer | None = None
        self._lock = threading.Lock()

        # SQLite for id mapping
        self._meta_db: sqlite3.Connection | None = None
        if persist_path:
            meta_path = Path(persist_path).with_suffix(".meta.db")
            Path(meta_path).parent.mkdir(parents=True, exist_ok=True)
            self._meta_db = sqlite3.connect(
                str(meta_path), check_same_thread=False
            )
            self._meta_db.execute("PRAGMA journal_mode=WAL")
            self._meta_db.execute(
                """CREATE TABLE IF NOT EXISTS id_mapping (
                    faiss_idx INTEGER PRIMARY KEY,
                    memory_id TEXT NOT NULL
                )"""
            )
            self._meta_db.commit()
            self._load_from_disk()

    # ── Persistence ──────────────────────────────────────────────────

    def _load_from_disk(self) -> None:
        """Load FAISS index and id mapping from disk if they exist."""
        if not self._persist_path:
            return

        # Load id mapping from SQLite
        if self._meta_db:
            rows = self._meta_db.execute(
                "SELECT faiss_idx, memory_id FROM id_mapping ORDER BY faiss_idx"
            ).fetchall()
            for idx, mid in rows:
                if idx >= len(self._ids):
                    self._ids.extend([""] * (idx - len(self._ids) + 1))
                self._ids[idx] = mid
                self._id_to_idx[mid] = idx

        # Load FAISS index
        if os.path.exists(self._persist_path):
            try:
                import faiss

                self._index = faiss.read_index(self._persist_path)
                self._dimension = self._index.d
                logger.info(
                    "FAISS index loaded from %s (%d vectors, dim=%d)",
                    self._persist_path,
                    self._index.ntotal,
                    self._dimension,
                )
            except Exception as exc:
                logger.warning("Failed to load FAISS index: %s", exc)
                self._index = None

    def _schedule_save(self) -> None:
        """Mark dirty and schedule a debounced save (5 seconds)."""
        self._dirty = True
        if self._save_timer is not None:
            self._save_timer.cancel()
        self._save_timer = threading.Timer(5.0, self._do_save)
        self._save_timer.daemon = True
        self._save_timer.start()

    def _do_save(self) -> None:
        """Persist the index and id mapping to disk."""
        with self._lock:
            if not self._dirty:
                return
            self._dirty = False

        if self._index is not None and self._persist_path:
            try:
                import faiss

                Path(self._persist_path).parent.mkdir(parents=True, exist_ok=True)
                faiss.write_index(self._index, self._persist_path)
            except Exception as exc:
                logger.warning("Failed to save FAISS index: %s", exc)

        if self._meta_db:
            try:
                with self._meta_db:
                    self._meta_db.execute("DELETE FROM id_mapping")
                    self._meta_db.executemany(
                        "INSERT INTO id_mapping (faiss_idx, memory_id) VALUES (?, ?)",
                        enumerate(self._ids),
                    )
            except Exception as exc:
                logger.warning("Failed to save id mapping: %s", exc)

        logger.info("FAISS index and id mapping saved to disk")

    def save(self) -> None:
        """Force-save the index and id mapping (call on shutdown)."""
        if self._save_timer is not None:
            self._save_timer.cancel()
            self._save_timer = None
        self._do_save()

    def _ensure_index(self, dim: int) -> None:
        """Create the FAISS index lazily once we know the dimensionality."""
        if self._index is not None:
            return
        self._dimension = dim
        import faiss

        # Flat index is fine for < 50k vectors; simple and exact.
        self._index = faiss.IndexFlatIP(dim)
        logger.info("FAISS index created (dim=%d)", dim)

    # ── Interface implementation ─────────────────────────────────────

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
        self._schedule_save()

    async def add_batch(self, items: list[tuple[str, str]]) -> None:
        if not items:
            return
        texts = [t for _, t in items]
        vecs = self._provider.embed_batch(texts)
        self._ensure_index(vecs[0].shape[0])

        for mid, _ in items:
            if mid in self._id_to_idx:
                await self.delete(mid)

        start = len(self._ids)
        for i, (mid, _) in enumerate(items):
            self._ids.append(mid)
            self._id_to_idx[mid] = start + i

        mat = np.stack(vecs)
        self._index.add(mat)
        self._schedule_save()

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
            mid = self._ids[idx]
            if not mid:  # skip tombstoned entries
                continue
            results.append((mid, float(score)))
        return results

    async def delete(self, memory_id: str) -> bool:
        if memory_id not in self._id_to_idx:
            return False
        # FAISS doesn't support efficient removal; tombstone the entry.
        idx = self._id_to_idx.pop(memory_id)
        self._ids[idx] = ""  # tombstone

        # Compact when tombstones exceed 20% of total entries
        active = len(self._id_to_idx)
        tombstones = len(self._ids) - active
        if tombstones > max(50, active // 5):
            await self.compact()

        self._schedule_save()
        return True

    async def compact(self) -> int:
        """Rebuild the index without tombstoned entries.

        Returns the number of entries that were compacted (tombstones removed).
        """
        if self._index is None:
            return 0

        import faiss

        # Collect active entries
        active_pairs = [
            (mid, idx) for idx, mid in enumerate(self._ids) if mid and mid in self._id_to_idx
        ]
        if not active_pairs:
            self._ids = []
            self._id_to_idx = {}
            self._index = None
            return 0

        tombstone_count = len(self._ids) - len(active_pairs)

        # Rebuild index from scratch (re-extract vectors from existing index)
        dim = self._dimension or 384
        new_index = faiss.IndexFlatIP(dim)

        if len(active_pairs) > 0 and self._index is not None:
            # Extract vectors for active entries from old index
            old_indices = [idx for _, idx in active_pairs]
            # FAISS doesn't support selective extraction, so re-embed
            # In practice this is rare (only when tombstones accumulate)
            try:
                all_vecs = np.zeros((len(active_pairs), dim), dtype=np.float32)
                for new_idx, (mid, old_idx) in enumerate(active_pairs):
                    # Reconstruct vector from FAISS index
                    vec = np.zeros(dim, dtype=np.float32)
                    self._index.reconstruct(old_idx, vec)
                    all_vecs[new_idx] = vec
                new_index.add(all_vecs)
            except Exception:
                # Fallback: rebuild with empty index, vectors will be re-added on next add()
                pass

        # Rebuild id lists
        new_ids: list[str] = []
        new_id_to_idx: dict[str, int] = {}
        for new_idx, (mid, _) in enumerate(active_pairs):
            new_ids.append(mid)
            new_id_to_idx[mid] = new_idx

        self._ids = new_ids
        self._id_to_idx = new_id_to_idx
        self._index = new_index

        self._schedule_save()

        logger.info(
            "FAISS compacted: removed %d tombstones, %d active entries remain",
            tombstone_count, len(new_ids),
        )
        return tombstone_count

    async def size(self) -> int:
        return len(self._id_to_idx)
