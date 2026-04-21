"""UnifiedRecall — 融合 Keyword + KG 双路径召回。

P4 统一 recall 接口，替代单一 keyword 或 KG 路径。

评分权重：
- keyword 权重 0.4
- KG 权重 0.6（KG 结果更精确，权重更高）

去重策略：以 memory_id 为 key，合并时取加权分。
"""

import asyncio
import logging

from src.memory.types import MemoryItem, MemoryType, MemoryScope

from .base import RecallStrategy

logger = logging.getLogger(__name__)


class UnifiedRecall(RecallStrategy):
    """融合召回策略：Keyword + KG 双路径。

    并发执行两条召回路径，按 memory_id 去重后以加权分数重排序。
    """

    def __init__(
        self,
        keyword_recall: RecallStrategy,
        kg_recall: RecallStrategy,
        keyword_weight: float = 0.4,
        kg_weight: float = 0.6,
    ) -> None:
        self._keyword = keyword_recall
        self._kg = kg_recall
        self._kw_weight = keyword_weight
        self._kg_weight = kg_weight

    async def recall(
        self,
        query: str,
        agent_id: str,
        session_id: str,
        memory_type: MemoryType | None,
        scope: MemoryScope | None,
        top_k: int,
    ) -> list[MemoryItem]:
        """Execute dual-path recall and merge results."""
        kw_task = self._safe_recall(
            self._keyword, query, agent_id, session_id,
            memory_type, scope, top_k,
        )
        kg_task = self._safe_recall(
            self._kg, query, agent_id, session_id,
            memory_type, scope, top_k,
        )

        kw_results, kg_results = await asyncio.gather(kw_task, kg_task)

        # Merge and deduplicate
        merged = self._merge(kw_results or [], kg_results or [])

        # Sort by combined score, take top_k
        merged.sort(
            key=lambda x: x.metadata.get("_combined_score", 0.0),
            reverse=True,
        )
        return merged[:top_k]

    async def _safe_recall(
        self,
        strategy: RecallStrategy,
        query: str,
        agent_id: str,
        session_id: str,
        memory_type: MemoryType | None,
        scope: MemoryScope | None,
        top_k: int,
    ) -> list[MemoryItem]:
        """Safely call a recall strategy, returning empty list on error."""
        try:
            return await strategy.recall(
                query, agent_id, session_id, memory_type, scope, top_k,
            )
        except Exception as exc:
            logger.warning(
                "Recall strategy %s failed: %s",
                type(strategy).__name__, exc,
            )
            return []

    def _merge(
        self,
        kw_results: list[MemoryItem],
        kg_results: list[MemoryItem],
    ) -> list[MemoryItem]:
        """Merge keyword and KG results, dedup by id.

        Scoring: each item gets a combined score based on which
        path(s) found it.  Items found by both paths get the full
        weighted sum; items from a single path get only their weight.
        The ``importance`` field serves as the base per-path score.
        """
        seen: dict[str, MemoryItem] = {}

        for item in kw_results:
            item.metadata["_kw_score"] = item.importance
            item.metadata["_combined_score"] = item.importance * self._kw_weight
            seen[item.id] = item

        for item in kg_results:
            score = item.importance
            if item.id in seen:
                existing = seen[item.id]
                existing.metadata["_kg_score"] = score
                existing.metadata["_combined_score"] = (
                    existing.metadata.get("_kw_score", 0.0) * self._kw_weight
                    + score * self._kg_weight
                )
                existing.metadata["_kg_match"] = True
            else:
                item.metadata["_kg_score"] = score
                item.metadata["_combined_score"] = score * self._kg_weight
                item.metadata["_kg_match"] = True
                seen[item.id] = item

        return list(seen.values())
