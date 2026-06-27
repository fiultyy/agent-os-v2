"""D-31 Endless Canvas — Layer 1 Observation + Layer 2 Control backend.

Core abstractions:
- **Tick**: 1 LLM request+response = 1 atomic unit of work.
- **Branch**: A divergent timeline forked from a parent tick.
- **Event**: Immutable fact in the event-sourcing log (SSE/WS broadcast).
- **EventStore**: SQLite-backed persistent event log.
- **SessionEventEmitter**: Dual-write (persist + broadcast) event emitter.
- **TickTracker**: Hooks into the LLM call layer to track tick lifecycle.
- **TabManager**: Maps browser tabs to session/branch for multi-tab support.
"""

from src.canvas.events import (
    CanvasEvent,
    TickStartedEvent,
    TokenDeltaEvent,
    ToolCallEvent,
    ToolResultEvent,
    TickCompletedEvent,
    BranchCreatedEvent,
    BranchMergedEvent,
)
from src.canvas.tick import Tick, TickStatus
from src.canvas.branch import Branch, BranchStatus
from src.canvas.event_store import CanvasEventStore
from src.canvas.emitter import SessionEventEmitter
from src.canvas.tracker import TickTracker
from src.canvas.tab_manager import TabManager

__all__ = [
    "CanvasEvent",
    "TickStartedEvent",
    "TokenDeltaEvent",
    "ToolCallEvent",
    "ToolResultEvent",
    "TickCompletedEvent",
    "BranchCreatedEvent",
    "BranchMergedEvent",
    "Tick",
    "TickStatus",
    "Branch",
    "BranchStatus",
    "CanvasEventStore",
    "SessionEventEmitter",
    "TickTracker",
    "TabManager",
]
