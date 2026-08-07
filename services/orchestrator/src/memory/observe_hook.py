"""MemoryObserveHook — bridges the internal memory event bus to observe-service.

Registers at ``HookPriority.OBSERVER`` (runs after the SYSTEM-priority
``DefaultMemoryHook``) and forwards every lifecycle event to the observe
WS ingest via an :class:`~src.harness.emit.ObserveEmitter`. Fire-and-forget:
emitter.emit is a no-op when the WS is not connected (ADR-7 zero-regression).

Mirrors the flow.py:96 smuggling pattern: observe's EventType enum only knows
``tick_*`` / ``tool_*`` / ``branch_*`` / ``token_*``, so memory lifecycle
events are wire-encoded as ``tick_completed`` with the real semantic in
``data.memory_event`` + ``data.memory_payload``. TUI / consumers classify by
``data.memory_event``.

``on_pre_compress`` returns ``None`` so the bus (which keeps the last
non-None result) preserves the ``CompressResult`` from DefaultMemoryHook —
this hook never masks compression.
"""

from __future__ import annotations

from typing import Any, Dict

from src.harness.emit import ObserveEmitter
from src.harness.events import _now
from src.memory.hooks import (
    CompressContext,
    ConsolidateContext,
    CurateContext,
    DelegateContext,
    HookPriority,
    IngestContext,
    MemoryHook,
    RecallContext,
    SessionContext,
    TurnContext,
)

# Fixed identity — memory lifecycle is global, not per-session.
_MEMORY_HARNESS_TYPE = "memory"
_MEMORY_HARNESS_ID = "memory_global"
_MEMORY_SESSION_ID = "memory"


def memory_event(event: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Build an observe event dict smuggling a memory lifecycle event.

    ponytail: dict builder, no dataclass — the wire format is the contract
    (mirrors harness/flow.py:flow_event + harness/events.py:_base).
    agent_id 从 payload.ctx.agent_id 提取(触发 turn 的 agent,非 side agent —
    memory hook 是无状态转发器,身份由 turn ctx 携带)。
    """
    return {
        "event_id": _event_id(),
        "harness_type": _MEMORY_HARNESS_TYPE,
        "harness_id": _MEMORY_HARNESS_ID,
        "session_id": _MEMORY_SESSION_ID,
        "agent_id": payload.get("agent_id", "") or "",
        "tick_id": "",
        "event_type": "tick_completed",
        "data": {
            "status": "success",
            "response": "",
            "memory_event": event,
            "memory_payload": payload,
        },
        "timestamp": _now(),
    }


def _event_id() -> str:
    import uuid

    return str(uuid.uuid4())


class MemoryObserveHook(MemoryHook):
    """OBSERVER-priority hook that forwards memory lifecycle events to observe."""

    priority = HookPriority.OBSERVER

    def __init__(self, emitter: ObserveEmitter) -> None:
        self._em = emitter

    async def on_session_start(self, ctx: SessionContext) -> None:
        await self._em.emit(memory_event("session_start", _ctx_payload(ctx)))

    async def on_turn_start(self, ctx: TurnContext) -> None:
        await self._em.emit(memory_event("turn_start", _ctx_payload(ctx)))

    async def on_turn_end(self, ctx: TurnContext) -> None:
        await self._em.emit(memory_event("turn_end", _ctx_payload(ctx)))

    async def on_pre_compress(self, ctx: CompressContext) -> None:
        # Returns None — bus keeps the CompressResult from DefaultMemoryHook.
        await self._em.emit(memory_event("pre_compress", _ctx_payload(ctx)))

    async def on_session_end(self, ctx: SessionContext) -> None:
        await self._em.emit(memory_event("session_end", _ctx_payload(ctx)))

    async def on_delegate(self, ctx: DelegateContext) -> None:
        await self._em.emit(memory_event("delegate", _ctx_payload(ctx)))

    async def on_ingest(self, ctx: IngestContext) -> None:
        await self._em.emit(memory_event("ingest", _ctx_payload(ctx)))

    async def on_consolidate(self, ctx: ConsolidateContext) -> None:
        await self._em.emit(memory_event("consolidate", _ctx_payload(ctx)))

    async def on_recall(self, ctx: RecallContext) -> None:
        await self._em.emit(memory_event("recall", _ctx_payload(ctx)))

    async def on_curate(self, ctx: CurateContext) -> None:
        await self._em.emit(memory_event("curate", _ctx_payload(ctx)))


def _ctx_payload(ctx: Any) -> Dict[str, Any]:
    """Flatten a hook context dataclass into a JSON-friendly dict.

    MemoryItem fields on TurnContext are filtered out (not JSON-friendly /
    large); only identity + trigger fields are forwarded to observe.
    ponytail: vars() dump minus heavy item fields — observe is a lifecycle
    signal, not a content transport.
    """
    if ctx is None:
        return {}
    drop = {"working_item", "tool_result_item", "conversation_item", "messages"}
    out: Dict[str, Any] = {}
    for k, v in getattr(ctx, "__dict__", {}).items():
        if k in drop:
            continue
        try:
            import json

            json.dumps(v)
            out[k] = v
        except (TypeError, ValueError):
            out[k] = str(v)
    return out
