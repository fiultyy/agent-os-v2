"""Tests for RuntimeObserverHook — error-spike detection + memory red-line.

Exercises the hook in isolation: constructs ``_state.execution_log`` steps and
asserts spike detection, session isolation, dedup, the non-fatal event-bus
contract, and that no observation ever reaches ``memory_service`` (R1-R8
red-line — observations stay in the in-memory ring buffer).
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from src.memory.event_bus import EventType, MemoryEventBus
from src.memory.hooks import HookPriority, MemoryHook, TurnContext
from src.memory.runtime_observer import RuntimeObserverHook
from src.services import _state


def _step(session_id: str, status: str = "done", agent_id: str = "a1") -> dict:
    return {
        "node_id": "llm",
        "agent_id": agent_id,
        "session_id": session_id,
        "status": status,
    }


def _seed(session_id: str, errors: int, oks: int, agent_id: str = "a1") -> None:
    """Append ``errors`` error steps then ``oks`` done steps for a session."""
    for _ in range(errors):
        _state.execution_log.append(_step(session_id, "error", agent_id))
    for _ in range(oks):
        _state.execution_log.append(_step(session_id, "done", agent_id))


@pytest.fixture(autouse=True)
def _reset_state():
    """Clear the module-level execution_log + runtime_observations each test."""
    _state.execution_log.clear()
    _state.runtime_observations.clear()
    yield
    _state.execution_log.clear()
    _state.runtime_observations.clear()


class TestRuntimeObserverSpike:
    @pytest.mark.asyncio
    async def test_spike_detected_at_threshold(self) -> None:
        _seed("s1", errors=3, oks=2)
        hook = RuntimeObserverHook()
        await hook.on_turn_end(TurnContext("a1", "s1"))
        kinds = [o["kind"] for o in _state.runtime_observations]
        assert "error_spike" in kinds

    @pytest.mark.asyncio
    async def test_no_spike_below_threshold(self) -> None:
        _seed("s1", errors=2, oks=4)
        hook = RuntimeObserverHook()
        await hook.on_turn_end(TurnContext("a1", "s1"))
        assert _state.runtime_observations == []

    @pytest.mark.asyncio
    async def test_no_spike_when_too_few_steps(self) -> None:
        # Fewer than threshold steps observed — don't cry wolf on a fresh session.
        _seed("s1", errors=2, oks=0)
        hook = RuntimeObserverHook()
        await hook.on_turn_end(TurnContext("a1", "s1"))
        assert _state.runtime_observations == []

    @pytest.mark.asyncio
    async def test_other_session_isolated(self) -> None:
        _seed("s1", errors=3, oks=2)
        _seed("s2", errors=0, oks=5, agent_id="a2")
        hook = RuntimeObserverHook()
        await hook.on_turn_end(TurnContext("a1", "s1"))
        await hook.on_turn_end(TurnContext("a2", "s2"))
        flagged = {o["agent_id"] for o in _state.runtime_observations}
        assert flagged == {"a1"}

    @pytest.mark.asyncio
    async def test_dedup_within_window(self) -> None:
        _seed("s1", errors=3, oks=2)
        hook = RuntimeObserverHook()
        await hook.on_turn_end(TurnContext("a1", "s1"))
        await hook.on_turn_end(TurnContext("a1", "s1"))  # immediate re-flag
        spikes = [o for o in _state.runtime_observations if o["kind"] == "error_spike"]
        assert len(spikes) == 1


class TestRuntimeObserverPriorityAndIsolation:
    def test_priority_is_observer(self) -> None:
        assert RuntimeObserverHook().priority == HookPriority.OBSERVER

    @pytest.mark.asyncio
    async def test_hook_exception_is_non_fatal_via_bus(self) -> None:
        # A failing OBSERVER hook must not break emit (event_bus contract).
        # RuntimeObserverHook itself shouldn't raise, so use a sibling bad hook.
        bus = MemoryEventBus()

        class _Boom(MemoryHook):
            priority = HookPriority.OBSERVER

            async def on_turn_end(self, ctx: TurnContext) -> None:
                raise RuntimeError("boom")

        bus.register(_Boom(), EventType.TURN_END)
        _seed("s1", errors=3, oks=2)
        await bus.emit(EventType.TURN_END, TurnContext("a1", "s1"))  # must not raise


class TestRuntimeObserverRedLine:
    @pytest.mark.asyncio
    async def test_record_observation_never_writes_memory(self, monkeypatch) -> None:
        # R1-R8 red-line: record_observation is pure in-memory; it must never
        # reach memory_service.store / recall.
        calls: list[str] = []

        class _Spy:
            def store(self, *a, **k):
                calls.append("store")

            def recall(self, *a, **k):
                calls.append("recall")

        monkeypatch.setattr(_state, "memory_service", _Spy())
        _seed("s1", errors=3, oks=2)
        await RuntimeObserverHook().on_turn_end(TurnContext("a1", "s1"))
        assert calls == []  # no memory_service touch


class TestRuntimeObservationBuffer:
    def test_ring_buffer_caps_at_max(self, monkeypatch) -> None:
        monkeypatch.setattr(_state, "MAX_RUNTIME_OBSERVATIONS", 3)
        for i in range(10):
            _state.record_observation("error_spike", "a1", {"i": i})
        assert len(_state.runtime_observations) == 3

    def test_recent_observations_newest_first(self) -> None:
        _state.record_observation("error_spike", "a1", {"i": 1})
        _state.record_observation("error_spike", "a1", {"i": 2})
        recent = _state.recent_observations(10)
        assert recent[0]["i"] == 2  # newest first; detail flattened to top level
        assert recent[1]["i"] == 1
