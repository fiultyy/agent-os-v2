"""mem-service embedding — OpenAI-compat REST providers (ADR-13 向量层 v6).

Local-first: LM Studio (port 16666, nomic-embed-text-v1.5, dim 768) default +
Ollama (11434, qwen3-embedding:4b, dim 2560) fallback. Both expose OpenAI-compat
``/v1/embeddings`` (LM Studio server 需 ``lms load`` 模型; Ollama 现成).

``embed(text)`` → list[float]; unreachable/empty → []. Passive providers (never
raise — caller falls back to next provider or treats as no signal). Memory cache
(text→vector) avoids re-embedding the same text.

Feasibility (实测 2026-08-07): synonym/rewrite pairs cosine 0.44-1.0 (nomic) vs
irrelevant 0.37-0.44 — 区分度 nomic 优于 qwen3(后者 syn/rew 与 irr 重叠)。
解 recall 字面盲区(synonym/rewrite hit@5=0%, ADR-9 baseline) — #2 向量层 query 侧正解。

ADR-13: provider 抽象 local-first(LM Studio 用户指定 + Ollama fallback), OpenAI-compat
``/v1/embeddings`` seam(新 provider slot in by 实现 embed).
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Embed text → float vector. Passive: error → []."""
    def embed(self, text: str) -> list[float]: ...


@dataclass
class OpenAICompatEmbedding:
    """OpenAI-compat ``/v1/embeddings`` provider (LM Studio / Ollama / OpenAI).

    Passive: network/parse errors → empty list. ``model`` is the provider's model
    id (LM Studio /v1/models id; Ollama tag). body shape: ``{"model","input"}``.
    """
    base_url: str
    model: str
    timeout: float = 30.0

    def embed(self, text: str) -> list[float]:
        body = json.dumps({"model": self.model, "input": text}).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/v1/embeddings", data=body,
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                doc = json.loads(resp.read().decode("utf-8", "replace"))
            data = doc.get("data") or []
            return list(data[0].get("embedding", [])) if data else []
        except (urllib.error.URLError, TimeoutError, OSError, ValueError,
                KeyError, IndexError):
            return []


# 默认 provider 列表: LM Studio(用户指定 nomic)优先, Ollama qwen3 fallback。
LM_STUDIO = OpenAICompatEmbedding(
    "http://127.0.0.1:16666", "text-embedding-nomic-embed-text-v1.5")
OLLAMA = OpenAICompatEmbedding(
    "http://127.0.0.1:11434", "qwen3-embedding:4b")


def default_providers() -> list[EmbeddingProvider]:
    """LM Studio nomic first, Ollama qwen3 fallback. ponytail: 两 provider 都
    local OpenAI-compat, 同 seam; unreachable 自剔除(embed 返 [])。"""
    return [LM_STUDIO, OLLAMA]


_cache: dict[str, list[float]] = {}


def embed(text: str, providers: list[EmbeddingProvider] | None = None) -> list[float]:
    """Embed ``text`` via providers (LM Studio default + Ollama fallback). Cached.

    Returns [] if no provider yields a vector (caller treats as no vec signal —
    recall should fall back to the字面/centrality/LIF score path).
    """
    if text in _cache:
        return _cache[text]
    for p in (providers if providers is not None else default_providers()):
        v = p.embed(text)
        if v:
            _cache[text] = v
            return v
    return []


def clear_cache() -> None:
    """Reset the in-memory cache (tests / db switch)."""
    _cache.clear()


def _demo() -> None:  # ponytail self-check
    v = embed("用户使用 rust")
    assert v, "no embedding — is LM Studio/Ollama running?"
    print("embed ok, dim:", len(v))


if __name__ == "__main__":
    _demo()
