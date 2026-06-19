"""Memory hooks — lifecycle hook contract + context types.

Six lifecycle events (aligned with the hermes-agent ``MemoryManager``
6-hook lifecycle): ``session_start`` / ``turn_start`` / ``turn_end`` /
``pre_compress`` / ``session_end`` / ``delegate``.

``chat.py`` emits these via :class:`~src.memory.event_bus.MemoryEventBus`;
:class:`~src.memory.default_hook.DefaultMemoryHook` (and any observer
hooks) react. P1 wires only ``session_start`` / ``turn_end`` /
``pre_compress`` / ``session_end``; ``turn_start`` (recall injection)
is reserved for P2 and ``delegate`` for P3/P4.

The bus is a *lifecycle orchestration* layer, not a CRUD proxy: chat.py
hands over turn-level memory side-effects and the hook executes the
actual ``memory_service`` / ``memory_migrator`` calls.
"""

from __future__ import annotations

from abc import ABC
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any

from src.memory.types import MemoryItem


class HookPriority(IntEnum):
    """Hook execution order within an event. Lower value runs first."""

    SYSTEM = 0  # core migrate/compress/store (DefaultMemoryHook) — runs first
    OBSERVER = 100  # audit/metrics extensions — run after core


# ── Context types ──────────────────────────────────────────────────


@dataclass
class SessionContext:
    """Session-bound event payload (session_start / session_end)."""

    agent_id: str
    session_id: str


@dataclass
class TurnContext:
    """Turn-level memory side-effects, executed by hooks in priority order.

    Each optional field carries a pre-built :class:`MemoryItem` whose
    construction stays in the route layer (it owns the business type/scope
    semantics); the hook only performs the store/migrate. At most one of
    the item fields is typically set per emit.
    """

    agent_id: str
    session_id: str
    # L0 working item from _node_llm → migrate working→session
    working_item: MemoryItem | None = None
    # tool-result working memory from _node_tool → store
    tool_result_item: MemoryItem | None = None
    # conversation session memory from simple chat() → store
    conversation_item: MemoryItem | None = None


@dataclass
class CompressContext:
    """Self-contained compression input.

    Carrying the raw message list lets the hook compute tokens and the
    trigger level itself, so compression logic is unit-testable without
    running the graph (P1 acceptance criterion).
    """

    agent_id: str
    session_id: str
    accessor_id: str
    messages: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class CompressResult:
    """Outcome of a pre_compress event, surfaced back to the caller.

    ``summary_ids`` mirrors chat.py's pre-P1 behaviour: SYNC compression
    returns the compressor-generated summary ids (appended to
    ``state.memory_refs``); ASYNC returns an empty list (the on_compressed
    callback fires later, off the caller's path).
    """

    triggered: bool = False
    level: str = "none"  # none / async / sync
    original_count: int = 0
    retained_count: int = 0
    summary_count: int = 0
    summary_ids: list[str] = field(default_factory=list)


@dataclass
class DelegateContext:
    """Reserved for P3/P4 sub-agent delegation."""

    agent_id: str
    session_id: str
    task: str = ""
    result: str = ""


# ── Hook contract ──────────────────────────────────────────────────


class MemoryHook(ABC):
    """Lifecycle hook base class.

    Subclasses override only the events they care about; every method
    has a no-op default so the bus can ``getattr`` any ``on_*`` handler
    safely.
    """

    priority: HookPriority = HookPriority.OBSERVER

    async def on_session_start(self, ctx: SessionContext) -> None:
        ...

    async def on_turn_start(self, ctx: TurnContext) -> None:
        # Reserved: recall injection lands here in P2 (compiler refactor).
        ...

    async def on_turn_end(self, ctx: TurnContext) -> None:
        ...

    async def on_pre_compress(self, ctx: CompressContext) -> CompressResult:
        return CompressResult()

    async def on_session_end(self, ctx: SessionContext) -> None:
        ...

    async def on_delegate(self, ctx: DelegateContext) -> None:
        # Reserved for P3/P4 sub-agent delegation.
        ...
