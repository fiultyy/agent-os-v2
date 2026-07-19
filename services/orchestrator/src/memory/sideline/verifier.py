# ARCHIVED — side-agent parallel mechanism (pre-AO2),不挂入系统,待用 AO2 capability 重接。
"""
RecallVerifier - recall 质量验证器

实现 D-25 中的 Sideline Verifier 层：
- 验证 recall 结果的质量
- 打分和排序
- 决定是否注入
"""

import logging
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger(__name__)


class RecallVerifier:
    """
    Recall 质量验证器

    设计原则：
    - 轻量级验证（非 LLM）
    - 基于规则的评分
    - 可扩展为 LLM 验证（性能考虑默认关闭）
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.use_llm = self.config.get("use_llm_verifier", False)
        self.min_confidence = self.config.get("min_confidence", 0.5)

    def score(
        self,
        memories: List[Dict[str, Any]],
        context: str
    ) -> float:
        """
        对 recall 结果评分

        Args:
            memories: 召回的记忆列表
            context: 当前上下文

        Returns:
            置信度分数 (0-1)
        """
        if not memories:
            return 0.0

        # 1. 数量评分
        count_score = min(1.0, len(memories) / 5.0)

        # 2. 相关性评分（简单规则）
        relevance_score = self._calc_relevance(memories, context)

        # 3. 多样性评分
        diversity_score = self._calc_diversity(memories)

        # 综合分数
        final_score = (
            count_score * 0.2 +
            relevance_score * 0.6 +
            diversity_score * 0.2
        )

        return min(1.0, max(0.0, final_score))

    def _calc_relevance(
        self,
        memories: List[Dict[str, Any]],
        context: str
    ) -> float:
        """计算与上下文的相关性"""
        if not context or not memories:
            return 0.5

        context_lower = context.lower()
        relevant_count = 0

        for mem in memories:
            content = mem.get("content", "").lower()
            title = mem.get("title", "").lower()

            # 简单匹配：上下文中是否有记忆中的关键词
            words = set(content.split() + title.split())
            context_words = set(context_lower.split())

            overlap = len(words & context_words)
            if overlap > 0:
                relevant_count += 1

        return relevant_count / len(memories) if memories else 0.0

    def _calc_diversity(self, memories: List[Dict[str, Any]]) -> float:
        """计算多样性（避免重复记忆）"""
        if len(memories) <= 1:
            return 1.0

        # 检查 memory_id 唯一性
        ids = [m.get("memory_id", "") for m in memories]
        unique_ids = len(set(ids))

        # 检查内容唯一性
        contents = [m.get("content", "")[:50] for m in memories]
        unique_contents = len(set(contents))

        # 综合多样性
        id_diversity = unique_ids / len(memories)
        content_diversity = unique_contents / len(memories)

        return (id_diversity + content_diversity) / 2.0

    def verify(
        self,
        memories: List[Dict[str, Any]],
        context: str
    ) -> Tuple[bool, float]:
        """
        验证记忆是否应该注入

        Args:
            memories: 召回的记忆
            context: 当前上下文

        Returns:
            (should_inject, confidence)
        """
        confidence = self.score(memories, context)
        should_inject = confidence >= self.min_confidence

        return should_inject, confidence

    def rank(
        self,
        memories: List[Dict[str, Any]],
        context: str
    ) -> List[Dict[str, Any]]:
        """
        对记忆重排

        Args:
            memories: 待排序的记忆
            context: 当前上下文

        Returns:
            排序后的记忆列表（深拷贝，不污染原始数据）
        """
        if not memories:
            return []

        import copy
        ranked = copy.deepcopy(memories)

        # 计算每个记忆的分数
        for mem in ranked:
            mem["_verifier_score"] = self._calc_relevance([mem], context)

        # 按分数排序
        return sorted(
            ranked,
            key=lambda x: x.get("_verifier_score", 0),
            reverse=True
        )