"""BackwardWriter — 将判断结果写回 Main Agent。

三通道：
- 慢：LLM 摘要 → episodic memory
- 中：关键词模式 → semantic memory
- 快：结构化指令 → working memory

参考架构文档 D-25。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from src.memory.service import MemoryService

logger = logging.getLogger(__name__)


class WriteBackChannel(Enum):
    """写回通道"""
    SLOW = "slow"       # LLM 摘要 → episodic
    MEDIUM = "medium"   # 关键词模式 → semantic
    FAST = "fast"       # 结构化指令 → working


@dataclass
class WriteBackResult:
    """写回结果"""
    channel: WriteBackChannel
    written: bool
    content: str
    confidence: float
    memory_id: str = ""


class BackwardWriter:
    """
    将 Sideline Verifier 的判断结果写回 Main Agent 三层记忆。

    三通道策略：
    - FAST (confidence ≥ 0.8): 结构化指令直接写入 working memory
      → 低延迟，Main Agent 下一轮即可见
    - MEDIUM (0.5 ≤ confidence < 0.8): 提取关键词写入 semantic memory
      → 中等延迟，建立实体/关系连接
    - SLOW (confidence < 0.5): LLM 摘要后写入 episodic memory
      → 高延迟，跨 session 积累经验

    设计原则（来自 SidelineMemoryAgent）：
    - 不依赖 LLM 遵循 prompt（用结构化 I/O）
    - 仅在慢通道调用 LLM 做摘要
    - 快/中通道均为同步确定性操作
    """

    # 慢通道 LLM prompt 模板
    _SUMMARIZE_PROMPT = (
        "请将以下记忆内容浓缩为一段简洁的摘要（不超过 100 字），"
        "保留核心信息和关键结论：\n\n{content}"
    )

    # 中通道关键词提取正则
    _KEYWORD_RE = re.compile(r'\b[a-zA-Z\u4e00-\u9fff]{2,}\b')

    def __init__(
        self,
        memory_service: MemoryService,
        llm_client: Any | None = None,
    ) -> None:
        """
        Args:
            memory_service: 记忆服务（用于写回）
            llm_client: 可选的 LLM 客户端（仅慢通道使用）
        """
        self._memory = memory_service
        self._llm = llm_client

    def choose_channel(self, confidence: float) -> WriteBackChannel:
        """根据 confidence 选择通道"""
        if confidence >= 0.8:
            return WriteBackChannel.FAST
        elif confidence >= 0.5:
            return WriteBackChannel.MEDIUM
        return WriteBackChannel.SLOW

    async def write(
        self,
        content: str,
        confidence: float,
        target: str,
        agent_id: str = "main",
    ) -> WriteBackResult:
        """
        将内容写回目标记忆。

        Args:
            content: 要写回的内容
            confidence: 置信度 (0-1)
            target: 目标标识（session_id 或 memory_id）
            agent_id: 目标 agent ID
        """
        channel = self.choose_channel(confidence)

        if channel == WriteBackChannel.FAST:
            return await self._write_fast(content, target, agent_id)
        elif channel == WriteBackChannel.MEDIUM:
            return await self._write_medium(content, target, agent_id)
        return await self._write_slow(content, target, agent_id)

    # ── Fast channel ─────────────────────────────────────────────

    async def _write_fast(
        self,
        content: str,
        target: str,
        agent_id: str,
    ) -> WriteBackResult:
        """
        快通道：结构化指令直接写入 working memory。

        Main Agent 下一轮对话即可访问，零延迟。
        """
        from src.memory.types import MemoryType, MemoryScope

        # 包装为结构化指令格式
        structured = f"[INJECT] {content.strip()}"

        try:
            ref = await self._memory.store(
                content=structured,
                agent_id=agent_id,
                session_id=target,
                memory_type=MemoryType.WORKING,
                scope=MemoryScope.AGENT,
                importance=0.9,
                metadata={"source": "sideline_verifier", "channel": "fast"},
            )
            return WriteBackResult(
                channel=WriteBackChannel.FAST,
                written=True,
                content=structured,
                confidence=0.9,
                memory_id=ref.id,
            )
        except Exception as exc:
            logger.error("Fast write failed: %s", exc)
            return WriteBackResult(
                channel=WriteBackChannel.FAST,
                written=False,
                content=content,
                confidence=0.9,
            )

    # ── Medium channel ───────────────────────────────────────────

    async def _write_medium(
        self,
        content: str,
        target: str,
        agent_id: str,
    ) -> WriteBackResult:
        """
        中通道：提取关键词写入 semantic memory。

        提取内容中的关键实体和模式，建立语义索引。
        """
        from src.memory.types import MemoryType, MemoryScope

        keywords = self._extract_keywords(content)
        keyword_str = ", ".join(keywords[:10])  # 最多保留 10 个关键词

        # 格式化为 semantic memory 条目
        semantic_content = f"[KEYWORDS] {keyword_str}\n[CONTENT] {content.strip()}"

        try:
            ref = await self._memory.store(
                content=semantic_content,
                agent_id=agent_id,
                session_id=target,
                memory_type=MemoryType.SEMANTIC,
                scope=MemoryScope.SESSION,
                importance=0.6,
                metadata={
                    "source": "sideline_verifier",
                    "channel": "medium",
                    "keywords": keywords[:10],
                },
            )
            return WriteBackResult(
                channel=WriteBackChannel.MEDIUM,
                written=True,
                content=semantic_content,
                confidence=0.6,
                memory_id=ref.id,
            )
        except Exception as exc:
            logger.error("Medium write failed: %s", exc)
            return WriteBackResult(
                channel=WriteBackChannel.MEDIUM,
                written=False,
                content=content,
                confidence=0.6,
            )

    def _extract_keywords(self, content: str) -> list[str]:
        """
        从内容中提取关键词。

        策略：
        1. 去除短词 (len < 2)
        2. 去除常见停用词
        3. 按词频排序，取 top N
        """
        stop_words = {
            "the", "a", "an", "is", "are", "was", "were", "be", "been",
            "have", "has", "had", "do", "does", "did", "will", "would",
            "could", "should", "may", "might", "must", "can", "this",
            "that", "these", "those", "i", "you", "he", "she", "it",
            "we", "they", "what", "which", "who", "when", "where", "why",
            "how", "all", "each", "every", "both", "few", "more", "most",
            "other", "some", "such", "no", "nor", "not", "only", "own",
            "same", "so", "than", "too", "very", "just", "but", "and",
            "or", "if", "then", "because", "as", "until", "while",
            "的", "是", "在", "了", "和", "与", "或", "但", "如果",
            "那么", "因为", "所以", "虽然", "但是", "而且", "以及",
        }

        words = self._KEYWORD_RE.findall(content.lower())
        freq: dict[str, int] = {}
        for w in words:
            if w not in stop_words and len(w) >= 2:
                freq[w] = freq.get(w, 0) + 1

        # 按频率降序，取 top 10
        sorted_words = sorted(freq.items(), key=lambda x: -x[1])
        return [w for w, _ in sorted_words[:10]]

    # ── Slow channel ─────────────────────────────────────────────

    async def _write_slow(
        self,
        content: str,
        target: str,
        agent_id: str,
    ) -> WriteBackResult:
        """
        慢通道：LLM 摘要后写入 episodic memory。

        仅在高置信度判断需要深度加工时使用。
        调用 LLM 生成简洁摘要，保留核心信息。
        """
        from src.memory.types import MemoryType, MemoryScope

        if self._llm is None:
            logger.warning("Slow channel requires LLM client but none provided, skipping")
            return WriteBackResult(
                channel=WriteBackChannel.SLOW,
                written=False,
                content=content,
                confidence=0.3,
            )

        prompt = self._SUMMARIZE_PROMPT.format(content=content)

        try:
            response = await self._llm.agenerate(prompt)
            summary = response.strip() if response else content[:100]
        except Exception as exc:
            logger.error("LLM summarization failed: %s", exc)
            # 降级：直接取原内容前 100 字符
            summary = content[:100]

        try:
            ref = await self._memory.store(
                content=summary,
                agent_id=agent_id,
                session_id=target,
                memory_type=MemoryType.EPISODIC,
                scope=MemoryScope.SESSION,
                importance=0.4,
                metadata={
                    "source": "sideline_verifier",
                    "channel": "slow",
                    "original_length": len(content),
                },
            )
            return WriteBackResult(
                channel=WriteBackChannel.SLOW,
                written=True,
                content=summary,
                confidence=0.3,
                memory_id=ref.id,
            )
        except Exception as exc:
            logger.error("Slow write failed: %s", exc)
            return WriteBackResult(
                channel=WriteBackChannel.SLOW,
                written=False,
                content=summary,
                confidence=0.3,
            )
