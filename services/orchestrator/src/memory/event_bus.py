"""MemoryEventBus — full lifecycle event dispatch (not just memory).

Despite the legacy ``Memory`` name (kept per ADR-4 to avoid a wide import
rename), this bus now carries the *whole agent lifecycle*: memory events
(``session_start`` / ``turn_end`` / ``pre_compress`` / …) *and* the
tool/turn/stop/subagent lifecycle (``tool_pre`` / ``tool_post`` /
``tool_post_fail`` / ``turn_submit`` / ``stop`` / ``subagent_stop``).
Registered hooks react in priority order. This decouples the chat route
and other emitters from direct ``memory_service`` / ``memory_migrator``
calls and gives P2 (cache) and P3 (state machine) clean insertion points.

Degradation switch: with ``MEMORY_EVENT_BUS_ENABLED=0`` (see engine.py),
:meth:`emit` keeps only ``SYSTEM``-priority hooks (``DefaultMemoryHook``)
and skips ``OBSERVER`` hooks (audit/metrics) — behaviourally equivalent
to pre-P1, since pre-P1 had no observer hooks. ``chat.py`` always calls
``bus.emit``; there is no duplicate direct-call code path.
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Any

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
    StopContext,
    SubagentContext,
    ToolContext,
    TurnContext,
)

logger = logging.getLogger(__name__)


class EventType(str, Enum):
    """Full agent lifecycle event types (memory + tool/turn/stop/subagent)."""

    SESSION_START = "session_start"
    TURN_START = "turn_start"  # reserved (recall injection, P2)
    TURN_END = "turn_end"
    PRE_COMPRESS = "pre_compress"
    SESSION_END = "session_end"
    DELEGATE = "delegate"  # reserved (P3/P4)
    # Step0 public base — side-agent (LLM) events. Side-agent hooks MUST
    # register explicitly for these (register(hook, EventType.INGEST)); the
    # default all-events mount would fan out 10 events to every hook.
    INGEST = "ingest"  # IngestorAgent: raw text → KG entities/relations + score
    CONSOLIDATE = "consolidate"  # ConsolidatorAgent: episodic → semantic merge
    RECALL = "recall"  # RetrieverAgent: match × lif_weight ranking
    CURATE = "curate"  # CuratorAgent: offline LLM QA (archive/merge/correct)
    # -- Tool / Turn / Stop / Subagent lifecycle (ADR-1 H1) ------------
    # Fire points land in H2 (node B); the bus only defines + dispatches
    # them. TOOL_PRE consumers (e.g. guardrail) may return {allow, reason}
    # to short-circuit; the emitter decides what to do with that.
    TOOL_PRE = "tool_pre"  # before ToolExecutor runs a tool
    TOOL_POST = "tool_post"  # after a successful tool run
    TOOL_POST_FAIL = "tool_post_fail"  # tool run raised (observe-able)
    TURN_SUBMIT = "turn_submit"  # user prompt submitted, pre-execution
    STOP = "stop"  # session/agent stop
    SUBAGENT_STOP = "subagent_stop"  # subagent (workflow/a2a) finished


_ALL_EVENTS: tuple[EventType, ...] = tuple(EventType)

# Map event → context type (documentation/typing aid; not enforced).
_EVENT_CONTEXT: dict[EventType, type] = {
    EventType.SESSION_START: SessionContext,
    EventType.TURN_START: TurnContext,
    EventType.TURN_END: TurnContext,
    EventType.PRE_COMPRESS: CompressContext,
    EventType.SESSION_END: SessionContext,
    EventType.DELEGATE: DelegateContext,
    EventType.INGEST: IngestContext,
    EventType.CONSOLIDATE: ConsolidateContext,
    EventType.RECALL: RecallContext,
    EventType.CURATE: CurateContext,
    EventType.TOOL_PRE: ToolContext,
    EventType.TOOL_POST: ToolContext,
    EventType.TOOL_POST_FAIL: ToolContext,
    EventType.TURN_SUBMIT: TurnContext,
    EventType.STOP: StopContext,
    EventType.SUBAGENT_STOP: SubagentContext,
}


class MemoryEventBus:
    """Dispatches lifecycle events to registered :class:`MemoryHook` instances.

    Hooks within an event run in ascending ``priority`` order (SYSTEM
    before OBSERVER). A hook raising is logged and skipped — one failing
    hook never blocks the others, mirroring the hermes non-fatal fan-out
    contract.
    """

    def __init__(self) -> None:
        self._hooks: dict[EventType, list[MemoryHook]] = {e: [] for e in EventType}
        self._enabled: bool = True

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, enabled: bool) -> None:
        """Toggle degradation mode (kept only for clarity; emit reads the flag)."""
        self._enabled = enabled

    def register(self, hook: MemoryHook, *events: EventType) -> None:
        """Register a hook for the given events (all events if none given).

        Per-event hook lists are kept sorted by ``priority`` ascending so
        SYSTEM hooks always run before OBSERVER hooks regardless of
        registration order.
        """
        target = events or _ALL_EVENTS
        for ev in target:
            self._hooks[ev].append(hook)
            self._hooks[ev].sort(key=lambda h: h.priority)

    def unregister(self, hook: MemoryHook) -> None:
        """Remove a hook from all events it was registered for."""
        for ev in EventType:
            lst = self._hooks[ev]
            while hook in lst:
                lst.remove(hook)

    def hooks(self, event: EventType) -> list[MemoryHook]:
        """Return the active hooks for an event (after degradation filter)."""
        hooks = self._hooks.get(event, [])
        if not self._enabled:
            # Degradation: keep only SYSTEM-priority core hooks
            # (DefaultMemoryHook migrate/compress/store); observer hooks
            # (audit/metrics) are skipped — equivalent to pre-P1.
            hooks = [h for h in hooks if h.priority == HookPriority.SYSTEM]
        return list(hooks)

    async def emit(self, event: EventType, ctx: Any) -> Any:
        """Dispatch ``ctx`` to every active hook for ``event`` in priority order.

        Returns the last non-None hook result (e.g. a :class:`CompressResult`
        from ``on_pre_compress``), or ``None`` for void events.
        """
        result: Any = None
        handler_name = f"on_{event.value}"
        for hook in self.hooks(event):
            handler = getattr(hook, handler_name, None)
            if handler is None:
                continue
            try:
                r = await handler(ctx)
            except Exception:
                logger.warning(
                    "memory hook %s.%s failed (non-fatal)",
                    type(hook).__name__,
                    handler_name,
                    exc_info=True,
                )
                continue
            if r is not None:
                result = r
        return result
