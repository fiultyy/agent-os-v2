"""Unit tests for MemoryEventBus — full lifecycle dispatch.

Covers the ADR-1 H1 additions: 6 new EventType values (TOOL_PRE / TOOL_POST /
TOOL_POST_FAIL / TURN_SUBMIT / STOP / SUBAGENT_STOP), their Context types,
the no-op MemoryHook handlers, plus the unchanged bus contract
(register / emit / priority / degradation) exercised through the new events.
"""

from __future__ import annotations

import pytest

from src.memory.event_bus import (
    _ALL_EVENTS,
    _EVENT_CONTEXT,
    EventType,
    MemoryEventBus,
)
from src.memory.hooks import (
    HookPriority,
    MemoryHook,
    StopContext,
    SubagentContext,
    ToolContext,
    TurnContext,
)

# ── New event definitions (ADR-1 H1) ───────────────────────────────


NEW_EVENTS = (
    EventType.TOOL_PRE,
    EventType.TOOL_POST,
    EventType.TOOL_POST_FAIL,
    EventType.TURN_SUBMIT,
    EventType.STOP,
    EventType.SUBAGENT_STOP,
)


def test_new_event_values_and_count():
    # 10 pre-existing + 6 new = 16.
    assert len(list(EventType)) == 16
    assert EventType.TOOL_PRE.value == "tool_pre"
    assert EventType.TOOL_POST.value == "tool_post"
    assert EventType.TOOL_POST_FAIL.value == "tool_post_fail"
    assert EventType.TURN_SUBMIT.value == "turn_submit"
    assert EventType.STOP.value == "stop"
    assert EventType.SUBAGENT_STOP.value == "subagent_stop"


def test_turn_start_still_reserved_value():
    # TURN_START is pre-existing and unchanged (B.T2 wires the fire point).
    assert EventType.TURN_START.value == "turn_start"


def test_event_context_mapping_covers_all_events():
    # Every event must map to a context type (the doc/typing aid invariant).
    for ev in EventType:
        assert ev in _EVENT_CONTEXT, f"{ev} missing from _EVENT_CONTEXT"
        assert isinstance(_EVENT_CONTEXT[ev], type)


def test_event_context_mapping_for_new_events():
    assert _EVENT_CONTEXT[EventType.TOOL_PRE] is ToolContext
    assert _EVENT_CONTEXT[EventType.TOOL_POST] is ToolContext
    assert _EVENT_CONTEXT[EventType.TOOL_POST_FAIL] is ToolContext
    # TURN_SUBMIT reuses TurnContext (ADR-1).
    assert _EVENT_CONTEXT[EventType.TURN_SUBMIT] is TurnContext
    assert _EVENT_CONTEXT[EventType.STOP] is StopContext
    assert _EVENT_CONTEXT[EventType.SUBAGENT_STOP] is SubagentContext


def test_all_events_constant_picks_up_new_events():
    # register(hook) with no events uses _ALL_EVENTS; the new keys must be
    # present so a default-mount hook lands on them too.
    for ev in NEW_EVENTS:
        assert ev in _ALL_EVENTS


# ── Context dataclass fields ───────────────────────────────────────


def test_tool_context_defaults():
    ctx = ToolContext(tool_name="bash")
    assert ctx.tool_name == "bash"
    assert ctx.arguments == {}
    assert ctx.result is None
    assert ctx.error is None
    # default-allowed: an absent TOOL_PRE consumer means "no veto".
    assert ctx.allow is True
    assert ctx.reason == ""


def test_stop_context_fields():
    ctx = StopContext(session_id="s1", agent_id="a1", reason="user")
    assert (ctx.session_id, ctx.agent_id, ctx.reason) == ("s1", "a1", "user")


def test_subagent_context_parent_linkage():
    ctx = SubagentContext(
        agent_id="child",
        session_id="child-sess",
        parent_session_id="parent-sess",
    )
    assert ctx.parent_session_id == "parent-sess"


# ── Bus register / emit (new events) ───────────────────────────────


class _RecordingHook(MemoryHook):
    """Captures every on_* call it participates in (for ordering/assertions)."""

    def __init__(self, name: str = "rec", priority=HookPriority.OBSERVER):
        self.name = name
        self.priority = priority
        self.calls: list[tuple[str, object]] = []

    async def on_tool_pre(self, ctx):
        self.calls.append(("tool_pre", ctx))
        return None

    async def on_tool_post(self, ctx):
        self.calls.append(("tool_post", ctx))

    async def on_tool_post_fail(self, ctx):
        self.calls.append(("tool_post_fail", ctx))

    async def on_turn_submit(self, ctx):
        self.calls.append(("turn_submit", ctx))

    async def on_stop(self, ctx):
        self.calls.append(("stop", ctx))

    async def on_subagent_stop(self, ctx):
        self.calls.append(("subagent_stop", ctx))


@pytest.mark.asyncio
async def test_register_and_emit_each_new_event():
    bus = MemoryEventBus()
    hook = _RecordingHook()
    bus.register(hook, *NEW_EVENTS)

    payloads = {
        EventType.TOOL_PRE: ToolContext(tool_name="bash", arguments={"cmd": "ls"}),
        EventType.TOOL_POST: ToolContext(
            tool_name="bash", result={"stdout": "ok"}
        ),
        EventType.TOOL_POST_FAIL: ToolContext(
            tool_name="bash", error=ValueError("boom")
        ),
        EventType.TURN_SUBMIT: TurnContext(agent_id="a1", session_id="s1"),
        EventType.STOP: StopContext(session_id="s1", reason="user"),
        EventType.SUBAGENT_STOP: SubagentContext(
            agent_id="child", session_id="cs", parent_session_id="ps"
        ),
    }
    for ev in NEW_EVENTS:
        await bus.emit(ev, payloads[ev])

    names = [c[0] for c in hook.calls]
    assert names == [
        "tool_pre",
        "tool_post",
        "tool_post_fail",
        "turn_submit",
        "stop",
        "subagent_stop",
    ]
    assert hook.calls[0][1] is payloads[EventType.TOOL_PRE]
    assert hook.calls[2][1].error.args == ("boom",)


@pytest.mark.asyncio
async def test_register_all_events_mounts_new_events():
    # register(hook) with no explicit events must land on the 6 new keys.
    bus = MemoryEventBus()
    hook = _RecordingHook()
    bus.register(hook)
    for ev in NEW_EVENTS:
        assert hook in bus.hooks(ev)


@pytest.mark.asyncio
async def test_emit_returns_last_non_none_for_tool_pre_veto():
    # A TOOL_PRE consumer may return {"allow": False, "reason": ...} to veto.
    class GuardHook(MemoryHook):
        priority = HookPriority.SYSTEM

        async def on_tool_pre(self, ctx):
            return {"allow": False, "reason": "blocked"}

    bus = MemoryEventBus()
    guard = GuardHook()
    bus.register(guard, EventType.TOOL_PRE)
    result = await bus.emit(
        EventType.TOOL_PRE, ToolContext(tool_name="rm", arguments={"path": "/"})
    )
    assert result == {"allow": False, "reason": "blocked"}


@pytest.mark.asyncio
async def test_emit_returns_none_when_no_consumer_replies():
    bus = MemoryEventBus()
    # No hook registered for STOP.
    result = await bus.emit(EventType.STOP, StopContext(session_id="s1"))
    assert result is None


# ── Priority: SYSTEM runs before OBSERVER (new events) ─────────────


@pytest.mark.asyncio
async def test_priority_system_before_observer_on_new_event():
    order: list[str] = []

    class ObsHook(MemoryHook):
        priority = HookPriority.OBSERVER

        async def on_stop(self, ctx):
            order.append("observer")

    class SysHook(MemoryHook):
        priority = HookPriority.SYSTEM

        async def on_stop(self, ctx):
            order.append("system")

    bus = MemoryEventBus()
    # Register OBSERVER first on purpose; sort must still put SYSTEM ahead.
    bus.register(ObsHook(), EventType.STOP)
    bus.register(SysHook(), EventType.STOP)
    await bus.emit(EventType.STOP, StopContext(session_id="s1"))
    assert order == ["system", "observer"]


# ── Degradation: MEMORY_EVENT_BUS_ENABLED=0 skips OBSERVER ─────────


@pytest.mark.asyncio
async def test_degradation_skips_observer_on_new_event():
    # set_enabled(False) simulates MEMORY_EVENT_BUS_ENABLED=0 (engine.py
    # toggles the same flag from env). OBSERVER hooks are filtered out;
    # SYSTEM hooks survive.
    calls: list[str] = []

    class ObsHook(MemoryHook):
        priority = HookPriority.OBSERVER

        async def on_tool_post(self, ctx):
            calls.append("obs")

    class SysHook(MemoryHook):
        priority = HookPriority.SYSTEM

        async def on_tool_post(self, ctx):
            calls.append("sys")

    bus = MemoryEventBus()
    bus.register(ObsHook(), EventType.TOOL_POST)
    bus.register(SysHook(), EventType.TOOL_POST)
    bus.set_enabled(False)

    active = bus.hooks(EventType.TOOL_POST)
    assert all(h.priority == HookPriority.SYSTEM for h in active)
    assert len(active) == 1

    await bus.emit(EventType.TOOL_POST, ToolContext(tool_name="bash"))
    assert calls == ["sys"]


@pytest.mark.asyncio
async def test_degradation_off_when_enabled_keeps_observer():
    # Sanity: enabled=True (default) keeps OBSERVER hooks on new events.
    bus = MemoryEventBus()
    assert bus.enabled is True

    class ObsHook(MemoryHook):
        priority = HookPriority.OBSERVER

    bus.register(ObsHook(), EventType.SUBAGENT_STOP)
    assert ObsHook() or True  # placeholder to keep class referenced
    active = bus.hooks(EventType.SUBAGENT_STOP)
    assert len(active) == 1


# ── Bus mechanism unchanged: non-fatal fan-out ─────────────────────


@pytest.mark.asyncio
async def test_failing_hook_does_not_block_others_on_new_event():
    # A hook raising is logged and skipped (mirrors pre-H1 contract).
    calls: list[str] = []

    class BoomHook(MemoryHook):
        priority = HookPriority.SYSTEM

        async def on_tool_post(self, ctx):
            raise RuntimeError("boom")

    class AfterHook(MemoryHook):
        priority = HookPriority.OBSERVER

        async def on_tool_post(self, ctx):
            calls.append("after")

    bus = MemoryEventBus()
    bus.register(BoomHook(), EventType.TOOL_POST)
    bus.register(AfterHook(), EventType.TOOL_POST)
    await bus.emit(EventType.TOOL_POST, ToolContext(tool_name="bash"))
    assert calls == ["after"]


# ── Base MemoryHook no-op defaults ─────────────────────────────────


@pytest.mark.asyncio
async def test_base_memory_hook_new_handlers_are_noop_return_none():
    # A bare MemoryHook subclass with no overrides must not raise on emit
    # for any new event, and returns None (no decision echoed).
    class BareHook(MemoryHook):
        pass

    bus = MemoryEventBus()
    hook = BareHook()
    bus.register(hook, *NEW_EVENTS)
    for ev, ctx in [
        (EventType.TOOL_PRE, ToolContext(tool_name="bash")),
        (EventType.TOOL_POST, ToolContext(tool_name="bash")),
        (EventType.TOOL_POST_FAIL, ToolContext(tool_name="bash")),
        (EventType.TURN_SUBMIT, TurnContext(agent_id="a", session_id="s")),
        (EventType.STOP, StopContext(session_id="s")),
        (
            EventType.SUBAGENT_STOP,
            SubagentContext(agent_id="a", session_id="s"),
        ),
    ]:
        result = await bus.emit(ev, ctx)
        assert result is None
