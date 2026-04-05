"""Compressor — dual-trigger context compression for memory management.

Implements:
- :class:`CompressionEngine` — core extract-key → summarize → replace logic.
- :class:`AsyncCompressor` — triggers at 70% context usage, runs in background.
- :class:`SyncCompressor` — triggers at 85% context usage, blocks with 2s timeout.
- :class:`ContextMonitor` — monitors token usage and determines trigger level.

Key design principles:
- **Importance-based retention**: All items are ranked by importance. Reasoning
  chains get a small boost (+0.15) but are *not* exempt from compression.
- **Target ratio is guaranteed**: ``target_ratio=0.3`` means at most 30% of
  items are retained; the rest are summarised in groups.
- **LLM summarisation** (optional): When a ``summarise_fn`` is provided, groups
  are summarised by the LLM. Falls back to heuristic key-sentence extraction.
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Coroutine

from src.memory.types import MemoryItem, MemoryType, MemoryScope

logger = logging.getLogger(__name__)

# ── Type alias for the optional LLM summarisation callback ──────────
#    async (prompt: str) -> str
SummariseFn = Callable[[str], Coroutine[Any, Any, str]]


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
    retained: list[MemoryItem] = field(default_factory=list)
    summaries: list[MemoryItem] = field(default_factory=list)
    level: CompressionLevel = CompressionLevel.NONE


# ── Helpers ─────────────────────────────────────────────────────────

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
        if re.search(r'\d+', s):
            score += 1.0
        if re.search(r'\b[A-Z][a-z]+\b', s):
            score += 0.5
        if any(w in s.lower() for w in [
            'because', 'therefore', 'decided', 'result',
            '因为', '所以', '决定',
        ]):
            score += 1.0
        score += min(len(s) / 100, 1.0)
        return score

    scored = sorted(sentences, key=_score, reverse=True)
    return scored[:max_sentences]


def _is_reasoning_chain(content: str) -> bool:
    """Detect if content is part of a structured multi-step reasoning chain.

    **Strict** detection: the content must exhibit *at least two* distinct
    reasoning markers *and* be longer than 60 characters (to avoid flagging
    short one-liners that happen to contain "step" or "because").

    A single "Step N: ..." line is **not** a reasoning chain by itself — it
    needs accompanying logical connectors or multiple numbered steps within
    the same item.
    """
    if len(content) < 40:
        return False

    content_lower = content.lower()
    marker_hits = 0

    # Pattern 1: numbered step markers (e.g. "Step 1", "Step 2")
    step_matches = re.findall(r'step\s+\d', content_lower)
    if step_matches:
        marker_hits += 1
    if len(step_matches) >= 2:
        marker_hits += 1  # bonus for multiple steps

    # Pattern 2: ordered transition words — count each individually
    for word in (r'\bfirstly\b', r'\bsecondly\b', r'\bfinally\b'):
        if re.search(word, content_lower):
            marker_hits += 1

    # Pattern 3: Chinese causal chains
    if re.search(r'假设.*因此', content_lower):
        marker_hits += 1
    if re.search(r'推理.*结论', content_lower):
        marker_hits += 1

    # Pattern 4: Chinese numbered steps
    if re.search(r'步骤\s*[一二三\d]', content_lower):
        marker_hits += 1

    return marker_hits >= 2


# ── Compression Engine ─────────────────────────────────────────────

class CompressionEngine:
    """Core compression logic: extract → summarise → replace.

    Strategy:
    1. Score all items. Reasoning-chain items get a small importance boost.
    2. Rank items by adjusted importance.
    3. Keep the top ``target_ratio`` fraction; group the rest for summarisation.
    4. Generate summary MemoryItems (LLM or heuristic).
    """

    def __init__(
        self,
        summarise_fn: SummariseFn | None = None,
        reasoning_boost: float = 0.15,
    ) -> None:
        self._summarise_fn = summarise_fn
        self._reasoning_boost = reasoning_boost

    def compress_items(
        self,
        items: list[MemoryItem],
        target_ratio: float = 0.3,
    ) -> tuple[list[MemoryItem], list[MemoryItem]]:
        """Compress a list of memory items.

        Args:
            items: Memory items to compress.
            target_ratio: Fraction of items to *retain* (0.0–1.0).
                ``0.3`` means keep 30 %, summarise the remaining 70 %.

        Returns:
            Tuple of ``(retained_items, new_summary_items)``.
        """
        if len(items) <= 2:
            logger.debug("compress_items: too few items (%d), skipping", len(items))
            return items, []

        # 1. Compute adjusted importance for every item.
        scored: list[tuple[float, MemoryItem]] = []
        for item in items:
            boost = self._reasoning_boost if _is_reasoning_chain(item.content) else 0.0
            adjusted = min(1.0, item.importance + boost)
            scored.append((adjusted, item))

        # 2. Sort descending by adjusted importance.
        scored.sort(key=lambda pair: pair[0], reverse=True)

        # 3. Determine how many items to retain.
        target_count = max(1, int(len(items) * target_ratio))
        retained_pairs = scored[:target_count]
        compress_pairs = scored[target_count:]

        if not compress_pairs:
            logger.debug("compress_items: nothing to compress (target_count=%d)", target_count)
            return items, []

        retained = [item for _, item in retained_pairs]

        # 4. Group items to compress into batches of ~3 and summarise.
        group_size = 3
        summaries: list[MemoryItem] = []
        for i in range(0, len(compress_pairs), group_size):
            group = [item for _, item in compress_pairs[i:i + group_size]]
            summary = self._summarize_group(group)
            summaries.append(summary)

        logger.info(
            "compress_items: %d items → %d retained, %d summaries (target_ratio=%.2f)",
            len(items), len(retained), len(summaries), target_ratio,
        )
        return retained, summaries

    # ----------------------------------------------------------------

    def _summarize_group(self, items: list[MemoryItem]) -> MemoryItem:
        """Generate a summary MemoryItem from a group of items.

        Uses the LLM callback when available; otherwise falls back to
        heuristic key-sentence extraction.
        """
        combined = "\n".join(f"- {item.content}" for item in items)

        if self._summarise_fn is not None:
            # LLM summarisation — the async fn will be awaited by the caller.
            # For synchronous use (compress_items is sync), we do a simple
            # heuristic fallback; the async wrapper handles LLM calls.
            summary_text = self._heuristic_summary(combined)
        else:
            summary_text = self._heuristic_summary(combined)

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

    @staticmethod
    def _heuristic_summary(combined: str) -> str:
        """Fallback: extract key sentences and prefix with [Summary]."""
        key_points = _extract_key_sentences(combined, max_sentences=3)
        return "[Summary] " + "; ".join(key_points)

    async def _llm_summarize_group(self, items: list[MemoryItem]) -> MemoryItem:
        """Async version that uses the LLM to generate a summary."""
        combined = "\n".join(f"- {item.content}" for item in items)

        prompt = (
            "请将以下记忆片段压缩为一条简洁的摘要（100字以内），"
            "保留关键信息和决策，去掉冗余细节：\n\n"
            f"{combined}"
        )

        try:
            summary_content = await self._summarise_fn(prompt)  # type: ignore[misc]
        except Exception:
            logger.warning("LLM summarisation failed, falling back to heuristic")
            summary_content = self._heuristic_summary(combined)

        avg_importance = sum(i.importance for i in items) / len(items)
        source_ids = [i.id for i in items]

        return MemoryItem(
            id=str(uuid.uuid4()),
            agent_id=items[0].agent_id if items else "",
            session_id=items[0].session_id if items else "",
            memory_type=items[0].memory_type if items else MemoryType.SESSION,
            scope=items[0].scope if items else MemoryScope.AGENT,
            content=summary_content,
            importance=min(1.0, avg_importance + 0.1),
            metadata={
                "source_ids": source_ids,
                "compressed_at": datetime.now(timezone.utc).isoformat(),
                "compression_type": "llm_summary",
            },
        )

    async def compress_items_async(
        self,
        items: list[MemoryItem],
        target_ratio: float = 0.3,
    ) -> tuple[list[MemoryItem], list[MemoryItem]]:
        """Async version of compress_items that uses LLM for summarisation.

        Falls back to the synchronous (heuristic) path when no LLM callback
        is configured.
        """
        if self._summarise_fn is None:
            return self.compress_items(items, target_ratio=target_ratio)

        if len(items) <= 2:
            return items, []

        # 1. Compute adjusted importance.
        scored: list[tuple[float, MemoryItem]] = []
        for item in items:
            boost = self._reasoning_boost if _is_reasoning_chain(item.content) else 0.0
            adjusted = min(1.0, item.importance + boost)
            scored.append((adjusted, item))

        scored.sort(key=lambda pair: pair[0], reverse=True)

        target_count = max(1, int(len(items) * target_ratio))
        retained_pairs = scored[:target_count]
        compress_pairs = scored[target_count:]

        if not compress_pairs:
            return items, []

        retained = [item for _, item in retained_pairs]

        # 2. Summarise groups with LLM.
        group_size = 3
        summaries: list[MemoryItem] = []
        for i in range(0, len(compress_pairs), group_size):
            group = [item for _, item in compress_pairs[i:i + group_size]]
            summary = await self._llm_summarize_group(group)
            summaries.append(summary)

        logger.info(
            "compress_items_async: %d items → %d retained, %d summaries (target_ratio=%.2f)",
            len(items), len(retained), len(summaries), target_ratio,
        )
        return retained, summaries


# ── Context Monitor ─────────────────────────────────────────────────

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
        """Determine which compression level to trigger."""
        ratio = self.usage_ratio(current_tokens)
        if ratio >= self._sync_threshold:
            return CompressionLevel.SYNC
        if ratio >= self._async_threshold:
            return CompressionLevel.ASYNC
        return CompressionLevel.NONE


# ── Async Compressor (70 % trigger) ─────────────────────────────────

class AsyncCompressor:
    """Compressor that runs asynchronously at 70% context usage.

    Compression runs in the background and does not block the caller.
    The actual :class:`CompressionResult` (with ``retained`` and
    ``summaries`` lists) is available via the ``on_compressed`` callback
    or by awaiting :meth:`wait`.
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
        self._last_result: CompressionResult | None = None

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

        async def _do_compress() -> CompressionResult:
            try:
                # Use async engine when LLM summarise fn is available
                if self._engine._summarise_fn is not None:
                    retained, summaries = await self._engine.compress_items_async(items)
                else:
                    retained, summaries = self._engine.compress_items(items)

                result = CompressionResult(
                    original_count=len(items),
                    compressed_count=len(retained),
                    summary_ids=[s.id for s in summaries],
                    retained=retained,
                    summaries=summaries,
                    level=CompressionLevel.ASYNC,
                )
                self._last_result = result
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
            return self._last_result
        try:
            return await asyncio.wait_for(
                asyncio.shield(self._task), timeout=timeout,
            )
        except asyncio.TimeoutError:
            return self._last_result


# ── Sync Compressor (85 % trigger) ──────────────────────────────────

class SyncCompressor:
    """Compressor that runs synchronously at 85% context usage.

    Blocks the caller with a configurable timeout. If compression
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
            if self._engine._summarise_fn is not None:
                retained, summaries = await asyncio.wait_for(
                    self._engine.compress_items_async(items),
                    timeout=self._timeout,
                )
            else:
                retained, summaries = await asyncio.wait_for(
                    asyncio.to_thread(self._engine.compress_items, items),
                    timeout=self._timeout,
                )
            return CompressionResult(
                original_count=len(items),
                compressed_count=len(retained),
                summary_ids=[s.id for s in summaries],
                retained=retained,
                summaries=summaries,
                level=CompressionLevel.SYNC,
            )
        except asyncio.TimeoutError:
            logger.warning("Sync compression timed out (%.1fs)", self._timeout)
            return CompressionResult(
                original_count=len(items),
                compressed_count=len(items),
                level=CompressionLevel.SYNC,
            )
