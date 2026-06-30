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


class ApiEmbeddingProvider:
    """Embedding provider backed by an OpenAI-compatible API.

    适配 LM Studio / 智谱 embedding-3 / OpenAI 等(POST {base_url}/embeddings)。
    由 env 切换:EMBEDDING_PROVIDER=api 时 get_default_provider() 返回本类。
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        dimensions: int = 0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._dimensions = dimensions  # 0 = 不发 dimensions 参数(用模型默认维度)
        self._dim_cached: int | None = None

    def _post(self, inputs: list[str]) -> list[np.ndarray]:
        import json
        import urllib.request

        payload: dict = {"model": self._model, "input": inputs}
        if self._dimensions and self._dimensions > 0:
            payload["dimensions"] = self._dimensions
        req = urllib.request.Request(
            f"{self._base_url}/embeddings",
            data=json.dumps(payload).encode(),
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
        vecs = [np.array(d["embedding"], dtype=np.float32) for d in data["data"]]
        # L2 归一化(与 SentenceTransformerProvider normalize_embeddings=True 对齐,余弦相似度所需)
        vecs = [v / (np.linalg.norm(v) + 1e-12) for v in vecs]
        if vecs:
            self._dim_cached = len(vecs[0])
        return vecs

    def embed(self, text: str) -> np.ndarray:
        return self._post([text])[0]

    def embed_batch(self, texts: list[str]) -> list[np.ndarray]:
        if not texts:
            return []
        return self._post(texts)

    @property
    def dimension(self) -> int:
        if self._dim_cached is None:
            self._post(["dimension-probe"])  # 触发一次请求推断实际维度
        return self._dim_cached or self._dimensions or 0


def get_default_provider() -> EmbeddingProvider:
    """按 env 选择 embedding provider。

    - EMBEDDING_PROVIDER=api(或配了 EMBEDDING_BASE_URL)→ ApiEmbeddingProvider
      (OpenAI 兼容:LM Studio / 智谱 embedding-3 / OpenAI)
    - 默认 / =sentence-transformers → SentenceTransformerProvider(本地,需 torch)

    范例(本地 LM Studio):
      EMBEDDING_BASE_URL=http://127.0.0.1:16666/v1
      EMBEDDING_API_KEY=lm-studio  (任意非空,LM Studio 不校验)
      EMBEDDING_MODEL=text-embedding-nomic-embed-text-v1.5
      EMBEDDING_DIMENSIONS=768  (0=用模型默认,不发 dimensions 参数)
    """
    kind = os.getenv("EMBEDDING_PROVIDER", "sentence-transformers").lower()
    base_url = os.getenv("EMBEDDING_BASE_URL", "")
    if kind == "api" or (kind != "sentence-transformers" and base_url):
        return ApiEmbeddingProvider(
            base_url=base_url or "http://127.0.0.1:16666/v1",
            api_key=os.getenv("EMBEDDING_API_KEY", "lm-studio"),
            model=os.getenv("EMBEDDING_MODEL", "text-embedding-nomic-embed-text-v1.5"),
            dimensions=int(os.getenv("EMBEDDING_DIMENSIONS", "0") or "0"),
        )
    return SentenceTransformerProvider()
