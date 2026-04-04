"""Embedding provider — generates vector embeddings for memory content.

Uses sentence-transformers (all-MiniLM-L6-v2) as the default provider.
The interface is designed to be swappable for other backends.
"""

from __future__ import annotations

import logging
from typing import Protocol

import numpy as np
import os

# Force CPU for sentence-transformers (avoids CUDA compatibility issues)
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

logger = logging.getLogger(__name__)


class EmbeddingProvider(Protocol):
    """Protocol for embedding backends."""

    def embed(self, text: str) -> np.ndarray:
        """Return a 1-D float32 embedding vector for *text*."""
        ...

    def embed_batch(self, texts: list[str]) -> list[np.ndarray]:
        """Return embeddings for a batch of texts."""
        ...

    @property
    def dimension(self) -> int:
        """Embedding vector dimensionality."""
        ...


class SentenceTransformerProvider:
    """Embedding provider backed by sentence-transformers.

    Defaults to ``all-MiniLM-L6-v2`` (384-dim, fast, good quality).
    The model is lazily loaded on first use.
    """

    def __init__(self, model_name: str = "all-MiniLM-L6-v2") -> None:
        self._model_name = model_name
        self._model = None

    def _ensure_model(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            logger.info("Loading embedding model: %s", self._model_name)
            self._model = SentenceTransformer(self._model_name, device="cpu")

    def embed(self, text: str) -> np.ndarray:
        self._ensure_model()
        vec: np.ndarray = self._model.encode(text, normalize_embeddings=True)
        return vec.astype(np.float32)

    def embed_batch(self, texts: list[str]) -> list[np.ndarray]:
        if not texts:
            return []
        self._ensure_model()
        vecs: np.ndarray = self._model.encode(
            texts, normalize_embeddings=True, batch_size=64,
        )
        return [v.astype(np.float32) for v in vecs]

    @property
    def dimension(self) -> int:
        self._ensure_model()
        return self._model.get_sentence_embedding_dimension()
