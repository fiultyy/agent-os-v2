"""Tests for MemoryEventBus — registration, dispatch, priority, degradation.

These exercise the bus in isolation (no MemoryService, no graph). The
compression-hook behaviour lives in test_memory_default_hook.py.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from src.memory.event_bus import EventType, MemoryEventBus
from src.memory.hooks import (
    CompressContext,
    CompressResult,
    HookPriority,
    MemoryHook,
    SessionContext,
    TurnContext,
)


class _RecordingHook(MemoryHook):
    """Records every on_* invocation, with a configurable priority."""

    def __init__(self, name: str, priority: HookPriority = HookPriority.OBSERVER) -> None:
        self.name = name
        self.priority = priority
        self.calls: list[str] = []

    async def on_session_start(self, ctx: SessionContext) -> None:
        self.calls.append("session_start")

    async def on_turn_end(self, ctx: TurnContext) -> None:
        self.calls.append("turn_end")

    async def on_pre_compress(self, ctx: CompressContext) -> CompressResult:
        self.calls.append("pre_compress")
        return CompressResult(triggered=True, level="sync", summary_ids=["s1"])


class _FailingHook(MemoryHook):
    async def on_session_start(self, ctx: SessionContext) -> None:
        raise RuntimeError("boom")


class TestRegistration:
    def test_register_defaults_to_all_events(self) -> None:
        bus = MemoryEventBus()
        h = _RecordingHook("h")
        bus.register(h)
        for ev in EventType:
            assert h in bus.hooks(ev)

    def test_register_specific_events_only(self) -> None:
        bus = MemoryEventBus()
        h = _RecordingHook("h")
        bus.register(h, EventType.TURN_END)
        assert h in bus.hooks(EventType.TURN_END)
        assert h not in bus.hooks(EventType.SESSION_START)


class TestDispatch:
    @pytest.mark.asyncio
    async def test_emit_calls_registered_handler(self) -> None:
        bus = MemoryEventBus()
        h = _RecordingHook("h")
        bus.register(h, EventType.SESSION_START, EventType.TURN_END)
        await bus.emit(EventType.SESSION_START, SessionContext("a", "s"))
        await bus.emit(EventType.TURN_END, TurnContext("a", "s"))
        assert h.calls == ["session_start", "turn_end"]

    @pytest.mark.asyncio
    async def test_emit_returns_last_non_none_result(self) -> None:
        bus = MemoryEventBus()
        bus.register(_RecordingHook("h"), EventType.PRE_COMPRESS)
        res = await bus.emit(
            EventType.PRE_COMPRESS, CompressContext("a", "s", "a", [])
        )
        assert res is not None
        assert res.triggered is True
        assert res.summary_ids == ["s1"]

    @pytest.mark.asyncio
    async def test_void_event_returns_none(self) -> None:
        bus = MemoryEventBus()
        bus.register(_RecordingHook("h"), EventType.SESSION_START)
        res = await bus.emit(EventType.SESSION_START, SessionContext("a", "s"))
        assert res is None

    @pytest.mark.asyncio
    async def test_unregistered_event_is_noop(self) -> None:
        bus = MemoryEventBus()
        res = await bus.emit(EventType.DELEGATE, None)
        assert res is None  # no hooks → no error, no result


class TestPriority:
    @pytest.mark.asyncio
    async def test_system_runs_before_observer_regardless_of_order(self) -> None:
        order: list[str] = []

        class _Obs(MemoryHook):
            priority = HookPriority.OBSERVER

            async def on_turn_end(self, ctx: TurnContext) -> None:
                order.append("observer")

        class _Sys(MemoryHook):
            priority = HookPriority.SYSTEM

            async def on_turn_end(self, ctx: TurnContext) -> None:
                order.append("system")

        bus = MemoryEventBus()
        bus.register(_Obs())  # registered first…
        bus.register(_Sys())  # …but SYSTEM must still run first
        await bus.emit(EventType.TURN_END, TurnContext("a", "s"))
        assert order == ["system", "observer"]


class TestDegradation:
    @pytest.mark.asyncio
    async def test_disabled_keeps_only_system_hooks(self) -> None:
        bus = MemoryEventBus()
        sys_h = _RecordingHook("sys", HookPriority.SYSTEM)
        obs_h = _RecordingHook("obs", HookPriority.OBSERVER)
        bus.register(sys_h, EventType.SESSION_START)
        bus.register(obs_h, EventType.SESSION_START)
        bus.set_enabled(False)
        await bus.emit(EventType.SESSION_START, SessionContext("a", "s"))
        assert sys_h.calls == ["session_start"]
        assert obs_h.calls == []  # observer skipped under degradation

    @pytest.mark.asyncio
    async def test_enabled_runs_all(self) -> None:
        bus = MemoryEventBus()
        sys_h = _RecordingHook("sys", HookPriority.SYSTEM)
        obs_h = _RecordingHook("obs", HookPriority.OBSERVER)
        bus.register(sys_h, EventType.SESSION_START)
        bus.register(obs_h, EventType.SESSION_START)
        await bus.emit(EventType.SESSION_START, SessionContext("a", "s"))
        assert sys_h.calls == ["session_start"]
        assert obs_h.calls == ["session_start"]


class TestResilience:
    @pytest.mark.asyncio
    async def test_failing_hook_does_not_block_others(self) -> None:
        bus = MemoryEventBus()
        good = _RecordingHook("good")
        bus.register(_FailingHook(), EventType.SESSION_START)
        bus.register(good, EventType.SESSION_START)
        await bus.emit(EventType.SESSION_START, SessionContext("a", "s"))
        assert good.calls == ["session_start"]  # failing hook didn't block

    @pytest.mark.asyncio
    async def test_unregister_removes_hook(self) -> None:
        bus = MemoryEventBus()
        h = _RecordingHook("h")
        bus.register(h, EventType.SESSION_START)
        bus.unregister(h)
        await bus.emit(EventType.SESSION_START, SessionContext("a", "s"))
        assert h.calls == []
