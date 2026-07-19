"""Unit tests for MemoryObserveHook — memory lifecycle → observe WS bridge.

Verifies the ADR-7 fire-and-forget smuggling pattern:
- Every bus EventType forwards to the emitter as ``tick_completed`` with
  ``data.memory_event`` carrying the real semantic.
- ``on_pre_compress`` returns None so the bus keeps the DefaultMemoryHook
  CompressResult (this hook never masks compression).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import pytest

from src.memory.event_bus import EventType, MemoryEventBus
from src.memory.hooks import CompressContext, CompressResult, HookPriority, MemoryHook, SessionContext
from src.memory.observe_hook import MemoryObserveHook


class _StubSystemCompressor(MemoryHook):
    """SYSTEM-priority stand-in for DefaultMemoryHook.on_pre_compress.

    Tests the bus contract (last non-None wins) without assembling the full
    DefaultMemoryHook dependency graph (migrator/compressor/context_monitor).
    """

    priority = HookPriority.SYSTEM

    async def on_pre_compress(self, ctx: CompressContext) -> CompressResult:
        return CompressResult(level="sync")


class FakeEmitter:
    """Collects every emitted observe event for assertion."""

    def __init__(self) -> None:
        self.events: list[dict] = []

    async def emit(self, event: dict) -> None:
        self.events.append(event)

    async def connect(self) -> bool:
        return True


def _last(payload_field: str, ev: dict):
    return ev["data"][payload_field]


@pytest.mark.asyncio
async def test_session_start_forwarded_as_tick_completed():
    """SESSION_START emits a tick_completed smuggling memory_event=session_start."""
    em = FakeEmitter()
    bus = MemoryEventBus()
    bus.register(MemoryObserveHook(em), EventType.SESSION_START)

    ctx = SessionContext(agent_id="agent-1", session_id="sess-1")
    await bus.emit(EventType.SESSION_START, ctx)

    assert len(em.events) == 1
    ev = em.events[0]
    assert ev["event_type"] == "tick_completed"
    assert ev["harness_type"] == "memory"
    assert _last("memory_event", ev) == "session_start"
    assert ev["data"]["memory_payload"]["agent_id"] == "agent-1"
    assert ev["data"]["memory_payload"]["session_id"] == "sess-1"


@pytest.mark.asyncio
async def test_pre_compress_does_not_mask_compress_result():
    """on_pre_compress returns None — DefaultMemoryHook CompressResult survives.

    The bus keeps the last non-None result. With DefaultMemoryHook (SYSTEM,
    runs first) returning a CompressResult and MemoryObserveHook (OBSERVER,
    runs after) returning None, bus.emit must return the CompressResult.
    """
    em = FakeEmitter()
    bus = MemoryEventBus()
    bus.register(_StubSystemCompressor())
    bus.register(MemoryObserveHook(em), EventType.PRE_COMPRESS)

    ctx = CompressContext(
        agent_id="agent-1",
        session_id="sess-1",
        accessor_id="acc-1",
        messages=[{"role": "user", "content": "hi"}],
    )
    result = await bus.emit(EventType.PRE_COMPRESS, ctx)

    # Hook forwarded the lifecycle event to observe.
    assert len(em.events) == 1
    assert _last("memory_event", em.events[0]) == "pre_compress"
    # And the CompressResult from DefaultMemoryHook still surfaces.
    assert result is not None, "CompressResult masked by observer hook"
    assert result.__class__.__name__ == "CompressResult"


@pytest.mark.asyncio
async def test_all_lifecycle_events_forwarded():
    """Every EventType the hook handles forwards to the emitter."""
    em = FakeEmitter()
    hook = MemoryObserveHook(em)

    session_ctx = SessionContext(agent_id="a", session_id="s")
    compress_ctx = CompressContext(
        agent_id="a", session_id="s", accessor_id="acc", messages=[]
    )

    await hook.on_session_start(session_ctx)
    await hook.on_session_end(session_ctx)
    await hook.on_pre_compress(compress_ctx)

    events = [_last("memory_event", ev) for ev in em.events]
    assert events == ["session_start", "session_end", "pre_compress"]
