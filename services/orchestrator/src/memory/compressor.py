"""Compressor — dual-trigger context compression for memory management.

Implements:
- :class:`AsyncCompressor` — triggers at 70% context usage, runs in background.
- :class:`SyncCompressor` — triggers at 85% context usage, blocks with 2s timeout.
- :class:`CompressionStrategy` — extracts key info → generates summary → replaces.
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from src.memory.types import MemoryItem, MemoryType, MemoryScope

logger = logging.getLogger(__name__)


class CompressionLevel(str, Enum):
    NONE = "none"
    ASYNC = "async"
    SYNC = "sync"


@dataclass
class CompressionResult:
    """Result of a compression operation."""
    original_count: int
    compressed_count: int
    summary_ids: list[str] = field(default_factory=list)
    level: CompressionLevel = CompressionLevel.NONE


def _estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars per token."""
    return max(1, len(text) // 4)


def _extract_key_sentences(text: str, max_sentences: int = 3) -> list[str]:
    """Extract the most important sentences from text.

    Simple heuristic: prefer sentences with numbers, names (capitalized),
    and causal/decision language.
    """
    sentences = re.split(r'[.!?。！？]\s*', text)
    sentences = [s.strip() for s in sentences if len(s.strip()) > 10]
    if not sentences:
        return [text[:200]] if text else []

    def _score(s: str) -> float:
        score = 0.0
        # Contains numbers/data
        if re.search(r'\d+', s):
            score += 1.0
        # Contains capitalized words (likely names/entities)
        if re.search(r'\b[A-Z][a-z]+\b', s):
            score += 0.5
        # Causal/decision language
        if any(w in s.lower() for w in ['because', 'therefore', 'decided', 'result', 'because', '因为', '所以', '决定']):
            score += 1.0
        # Length bonus (longer = more info)
        score += min(len(s) / 100, 1.0)
        return score

    scored = sorted(sentences, key=_score, reverse=True)
    return scored[:max_sentences]


def _is_reasoning_chain(content: str) -> bool:
    """Detect if content is part of a multi-step reasoning chain.

    Only matches structured reasoning patterns — not isolated sentences
    that happen to contain "because" or a number.
    """
    content_lower = content.lower()
    # Must have at least 2 distinct reasoning markers to qualify
    reasoning_markers = [
        r'step\s+\d',
        r'firstly|secondly|finally',  # ordered transition words
        r'假设.*因此',                  # Chinese causal chain
        r'推理.*结论',                  # Chinese reasoning chain
        r'步骤\s*[一二三\d]',           # Chinese numbered steps
    ]
    marker_hits = sum(1 for p in reasoning_markers if re.search(p, content_lower))
    # Also count explicit multi-step patterns (e.g. "Step 1... Step 2...")
    if re.search(r'step\s+\d', content_lower) and re.search(r'step\s+\d', content_lower[content_lower.index('step') + 4:] if 'step' in content_lower else ''):
        marker_hits += 1
    return marker_hits >= 2


class CompressionEngine:
    """Core compression logic: extract → summarize → replace.

    Strategy:
    1. Group consecutive non-reasoning items.
    2. Extract key sentences from each group.
    3. Generate a summary MemoryItem.
    4. Preserve reasoning chain items unchanged.
    """

    def compress_items(
        self,
        items: list[MemoryItem],
        target_ratio: float = 0.3,
    ) -> tuple[list[MemoryItem], list[MemoryItem]]:
        """Compress a list of memory items.

        Args:
            items: Memory items to compress.
            target_ratio: Target ratio of compressed/original count.

        Returns:
            Tuple of (retained_items, new_summary_items).
        """
        if len(items) <= 2:
            return items, []

        # Separate reasoning chains from regular content
        reasoning: list[MemoryItem] = []
        regular: list[MemoryItem] = []
        for item in items:
            if _is_reasoning_chain(item.content):
                reasoning.append(item)
            else:
                regular.append(item)

        # Always keep reasoning chains
        target_count = max(1, int(len(items) * target_ratio))
        # Reasoning items count against the target
        remaining_slots = max(0, target_count - len(reasoning))

        if not regular or remaining_slots >= len(regular):
            # All regular items fit within slots, no compression needed
            return items, []

        # Must compress: keep `remaining_slots` best regular items,
        # summarize the rest in groups
        regular_sorted = sorted(regular, key=lambda g: g.importance, reverse=True)
        retained_regular = regular_sorted[:remaining_slots]
        to_compress = regular_sorted[remaining_slots:]

        if not to_compress:
            return reasoning + retained_regular, []

        # Group items to compress into batches of ~3 and summarize each batch
        group_size = max(2, 3)
        summaries: list[MemoryItem] = []
        for i in range(0, len(to_compress), group_size):
            group = to_compress[i:i + group_size]
            summary = self._summarize_group(group)
            summaries.append(summary)

        return reasoning + retained_regular, summaries

    def _summarize_group(self, items: list[MemoryItem]) -> MemoryItem:
        """Generate a summary MemoryItem from a group of items."""
        combined = "\n".join(item.content for item in items)
        key_points = _extract_key_sentences(combined, max_sentences=3)

        summary_text = "[Summary] " + "; ".join(key_points)

        # Inherit metadata from the group
        avg_importance = sum(i.importance for i in items) / len(items)
        source_ids = [i.id for i in items]

        return MemoryItem(
            id=str(uuid.uuid4()),
            agent_id=items[0].agent_id if items else "",
            session_id=items[0].session_id if items else "",
            memory_type=items[0].memory_type if items else MemoryType.SESSION,
            scope=items[0].scope if items else MemoryScope.AGENT,
            content=summary_text,
            importance=min(1.0, avg_importance + 0.1),
            metadata={
                "source_ids": source_ids,
                "compressed_at": datetime.now(timezone.utc).isoformat(),
                "compression_type": "summary",
            },
        )


class ContextMonitor:
    """Monitors context usage and triggers compression."""

    def __init__(
        self,
        max_context_tokens: int = 128_000,
        async_threshold: float = 0.70,
        sync_threshold: float = 0.85,
    ) -> None:
        self._max_tokens = max_context_tokens
        self._async_threshold = async_threshold
        self._sync_threshold = sync_threshold

    def usage_ratio(self, current_tokens: int) -> float:
        """Return current context usage as a ratio [0, 1]."""
        return min(1.0, current_tokens / self._max_tokens)

    def check_trigger(self, current_tokens: int) -> CompressionLevel:
        """Determine which compression level to trigger.

        Returns:
            - ``NONE`` if usage is below async threshold.
            - ``ASYNC`` if usage is between async and sync thresholds.
            - ``SYNC`` if usage exceeds sync threshold.
        """
        ratio = self.usage_ratio(current_tokens)
        if ratio >= self._sync_threshold:
            return CompressionLevel.SYNC
        if ratio >= self._async_threshold:
            return CompressionLevel.ASYNC
        return CompressionLevel.NONE


class AsyncCompressor:
    """Compressor that runs asynchronously at 70% context usage.

    Compression runs in the background and does not block the caller.
    """

    def __init__(
        self,
        engine: CompressionEngine | None = None,
        monitor: ContextMonitor | None = None,
    ) -> None:
        self._engine = engine or CompressionEngine()
        self._monitor = monitor or ContextMonitor()
        self._running = False
        self._task: asyncio.Task | None = None

    @property
    def is_running(self) -> bool:
        return self._running

    def check(self, current_tokens: int) -> bool:
        """Check if async compression should trigger."""
        level = self._monitor.check_trigger(current_tokens)
        return level == CompressionLevel.ASYNC and not self._running

    async def trigger(
        self,
        items: list[MemoryItem],
        on_compressed: Any = None,
    ) -> CompressionResult:
        """Start async compression in the background.

        Args:
            items: Memory items to potentially compress.
            on_compressed: Optional callback ``async def(retained, summaries)``.
        """
        if self._running:
            return CompressionResult(
                original_count=len(items),
                compressed_count=len(items),
                level=CompressionLevel.ASYNC,
            )

        self._running = True

        async def _do_compress():
            try:
                retained, summaries = self._engine.compress_items(items)
                result = CompressionResult(
                    original_count=len(items),
                    compressed_count=len(retained),
                    summary_ids=[s.id for s in summaries],
                    level=CompressionLevel.ASYNC,
                )
                if on_compressed:
                    await on_compressed(retained, summaries)
                return result
            finally:
                self._running = False

        self._task = asyncio.create_task(_do_compress())
        return CompressionResult(
            original_count=len(items),
            compressed_count=len(items),
            level=CompressionLevel.ASYNC,
        )

    async def wait(self, timeout: float = 5.0) -> CompressionResult | None:
        """Wait for the async compression to complete."""
        if self._task is None:
            return None
        try:
            return await asyncio.wait_for(
                asyncio.shield(self._task), timeout=timeout,
            )
        except asyncio.TimeoutError:
            return None


class SyncCompressor:
    """Compressor that runs synchronously at 85% context usage.

    Blocks the caller with a 2-second timeout. If compression
    doesn't complete in time, returns the original items unchanged.
    """

    def __init__(
        self,
        engine: CompressionEngine | None = None,
        monitor: ContextMonitor | None = None,
        timeout_seconds: float = 2.0,
    ) -> None:
        self._engine = engine or CompressionEngine()
        self._monitor = monitor or ContextMonitor()
        self._timeout = timeout_seconds

    def check(self, current_tokens: int) -> bool:
        """Check if sync compression should trigger."""
        return self._monitor.check_trigger(current_tokens) == CompressionLevel.SYNC

    async def compress(
        self,
        items: list[MemoryItem],
    ) -> CompressionResult:
        """Run compression synchronously with timeout.

        If compression exceeds the timeout, returns items unchanged.
        """
        try:
            retained, summaries = await asyncio.wait_for(
                asyncio.to_thread(self._engine.compress_items, items),
                timeout=self._timeout,
            )
            return CompressionResult(
                original_count=len(items),
                compressed_count=len(retained),
                summary_ids=[s.id for s in summaries],
                level=CompressionLevel.SYNC,
            )
        except asyncio.TimeoutError:
            logger.warning("Sync compression timed out (%.1fs)", self._timeout)
            return CompressionResult(
                original_count=len(items),
                compressed_count=len(items),
                level=CompressionLevel.SYNC,
            )
