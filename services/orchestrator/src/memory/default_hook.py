"""DefaultMemoryHook — encapsulates the existing memory lifecycle logic.

Moved verbatim in behaviour from ``chat.py`` (L122–206 / L233 / L370–388 /
L410): migrate working→session, context compression (sync/async), session→
episodic migration, and session creation. ``chat.py`` now emits events;
this hook performs the actual ``memory_service`` / ``memory_migrator``
calls. SSE notifications (``emit_memory_event``) originate here, which
decouples the internal event bus from the SSE push layer.

Construction-injected dependencies make the hook unit-testable without
``_state``: tests pass a real or fake ``MemoryService`` /
``MemoryMigrator`` / compressors.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from src.memory.compressor import CompressionLevel
from src.memory.hooks import (
    CompressContext,
    CompressResult,
    HookPriority,
    MemoryHook,
    SessionContext,
    TurnContext,
)

logger = logging.getLogger(__name__)


def _estimate_tokens(messages: list[dict[str, Any]]) -> int:
    """Rough token estimate mirroring chat.py's pre-P1 heuristic (~4 chars/token)."""
    return sum(max(1, len(m.get("content", "")) // 4) for m in messages)


class DefaultMemoryHook(MemoryHook):
    """Core SYSTEM-priority hook that owns memory side-effects.

    behaviourally identical to the pre-P1 inline code in ``chat.py``; the
    only change is that it is now invoked through the event bus instead of
    being called directly from the route handler.
    """

    priority = HookPriority.SYSTEM

    def __init__(
        self,
        memory_service: Any,
        memory_migrator: Any,
        sync_compressor: Any,
        async_compressor: Any,
        context_monitor: Any,
    ) -> None:
        self._memory = memory_service
        self._migrator = memory_migrator
        self._sync_compressor = sync_compressor
        self._async_compressor = async_compressor
        self._monitor = context_monitor

    # Lazy import keeps the hook importable / testable without the global
    # _state holder being initialised first.
    @staticmethod
    def _emit_sse(event: str, details: dict[str, Any]) -> None:
        from src.services import _state

        _state.emit_memory_event(event, details)

    # ── session_start ───────────────────────────────────────────────

    async def on_session_start(self, ctx: SessionContext) -> None:
        await self._memory.create_session(ctx.session_id, ctx.agent_id)

    # ── turn_end ────────────────────────────────────────────────────

    async def on_turn_end(self, ctx: TurnContext) -> None:
        if ctx.working_item is not None:
            # _node_llm: migrate L0 working → L1 session (migrator scores).
            await self._migrator.migrate_working_to_session(
                ctx.working_item, ctx.session_id, ctx.agent_id,
            )
        if ctx.tool_result_item is not None:
            # _node_tool: store tool-result working memory.
            item = ctx.tool_result_item
            await self._memory.store(
                content=item.content,
                agent_id=item.agent_id,
                session_id=item.session_id,
                memory_type=item.memory_type,
                scope=item.scope,
                metadata=item.metadata,
            )
        if ctx.conversation_item is not None:
            # simple chat(): store the turn as session memory.
            item = ctx.conversation_item
            await self._memory.store(
                content=item.content,
                agent_id=item.agent_id,
                session_id=item.session_id,
                memory_type=item.memory_type,
                scope=item.scope,
            )

    # ── pre_compress ────────────────────────────────────────────────

    async def on_pre_compress(self, ctx: CompressContext) -> CompressResult:
        total_tokens = _estimate_tokens(ctx.messages)
        trigger_level = self._monitor.check_trigger(total_tokens)

        if trigger_level == CompressionLevel.SYNC:
            return await self._compress_sync(ctx)
        if trigger_level == CompressionLevel.ASYNC:
            return await self._compress_async(ctx)
        return CompressResult(level="none")

    async def _compress_sync(self, ctx: CompressContext) -> CompressResult:
        try:
            items = await self._memory.recall(
                query="",
                agent_id=ctx.agent_id,
                session_id=ctx.session_id,
                top_k=50,
            )
            result = await self._sync_compressor.compress(items)
            # Mirror pre-P1 chat.py: collect the compressor-generated
            # summary ids (NOT the store-returned ref ids) into summary_ids
            # so the caller appends the same values to memory_refs.
            summary_ids: list[str] = []
            for summary_item in result.summaries:
                await self._memory.store(
                    content=summary_item.content,
                    agent_id=summary_item.agent_id,
                    session_id=summary_item.session_id,
                    memory_type=summary_item.memory_type,
                    scope=summary_item.scope,
                    importance=summary_item.importance,
                    metadata=summary_item.metadata,
                )
                summary_ids.append(summary_item.id)
            if result.summaries:
                retained_ids = {r.id for r in result.retained}
                for item in items:
                    if item.id not in retained_ids:
                        await self._memory.update(
                            item.id, accessor_id=ctx.accessor_id, archived=True,
                        )
        except asyncio.TimeoutError:
            # Sync compression timed out — nothing landed, no SSE.
            return CompressResult(level="sync")

        self._emit_sse(
            "compress",
            {
                "agent_id": ctx.agent_id,
                "level": "sync",
                "original_count": result.original_count,
                "retained_count": result.compressed_count,
                "summary_count": len(result.summaries),
            },
        )
        return CompressResult(
            triggered=True,
            level="sync",
            original_count=result.original_count,
            retained_count=result.compressed_count,
            summary_count=len(result.summaries),
            summary_ids=summary_ids,
        )

    async def _compress_async(self, ctx: CompressContext) -> CompressResult:
        items = await self._memory.recall(
            query="",
            agent_id=ctx.agent_id,
            session_id=ctx.session_id,
            top_k=50,
        )

        async def _on_compressed(retained: list, summaries: list) -> None:
            for summary_item in summaries:
                await self._memory.store(
                    content=summary_item.content,
                    agent_id=summary_item.agent_id,
                    session_id=summary_item.session_id,
                    memory_type=summary_item.memory_type,
                    scope=summary_item.scope,
                    importance=summary_item.importance,
                    metadata=summary_item.metadata,
                )
            retained_ids = {r.id for r in retained}
            for item in items:
                if item.id not in retained_ids:
                    await self._memory.update(
                        item.id, accessor_id=ctx.accessor_id, archived=True,
                    )
            self._emit_sse(
                "compress",
                {
                    "agent_id": ctx.agent_id,
                    "level": "async",
                    "original_count": len(items),
                    "retained_count": len(retained),
                    "summary_count": len(summaries),
                },
            )

        await self._async_compressor.trigger(items, on_compressed=_on_compressed)
        # ASYNC fires in the background; summary ids land via the callback
        # after this returns, so summary_ids stays empty (matches pre-P1).
        return CompressResult(
            triggered=True,
            level="async",
            original_count=len(items),
        )

    # ── session_end ─────────────────────────────────────────────────

    async def on_session_end(self, ctx: SessionContext) -> None:
        ids = await self._migrator.migrate_session_to_episodic(
            ctx.session_id, ctx.agent_id,
        )
        if ids:
            self._emit_sse(
                "migrate",
                {
                    "agent_id": ctx.agent_id,
                    "path": "session_to_episodic",
                    "count": len(ids),
                    "ids": ids[:10],
                },
            )
