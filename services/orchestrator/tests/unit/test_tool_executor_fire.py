"""T1 (ADR-2/ADR-3): ToolExecutor fire-point + registered-guardrail behaviour.

Covers the H3 migration from hard-coded ``guardrail.check`` to bus-registered
guardrail, and the H2 TOOL_PRE/POST/POST_FAIL fire points:

* TOOL_PRE veto (registered Guardrail) → ``status=blocked``
* TOOL_POST veto → ``status=blocked_output``
* exception / timeout → TOOL_POST_FAIL fired + status preserved
* bus=None (legacy test shape) → no fire, no guardrail, tool still runs
* red line: executor no longer holds/references a Guardrail attribute
"""

from __future__ import annotations

import asyncio

import pytest

from src.memory.event_bus import EventType, MemoryEventBus
from src.memory.hooks import HookPriority, MemoryHook, ToolContext
from src.tools.executor import ToolExecutor
from src.tools.guardrail import Guardrail
from src.tools.registry import ToolRegistry


def _registry(handler) -> ToolRegistry:
    reg = ToolRegistry()
    reg.register("probe", handler, description="d", parameters={"type": "object"})
    return reg


def _run(coro):
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


# ── H3: registered guardrail drives the decision ──────────────────────


def test_tool_pre_guardrail_veto_returns_blocked():
    """A Guardrail registered on TOOL_PRE returning allow=False short-
    circuits to ``status=blocked`` without invoking the handler."""
    invoked = []

    async def _handler(**kwargs):
        invoked.append(True)
        return "ran"

    bus = MemoryEventBus()
    bus.register(Guardrail(), EventType.TOOL_PRE, EventType.TOOL_POST)
    ex = ToolExecutor(_registry(_handler), bus=bus)

    result = _run(ex.execute("probe", {"cmd": "rm -rf /tmp/x"}))
    assert result["status"] == "blocked"
    assert "Guardrail blocked" in result["error"]
    assert invoked == []  # handler never ran


def test_tool_post_guardrail_veto_returns_blocked_output():
    """A Guardrail on TOOL_POST detecting sensitive output →
    ``status=blocked_output``; handler ran, result withheld."""
    bus = MemoryEventBus()
    bus.register(Guardrail(), EventType.TOOL_PRE, EventType.TOOL_POST)

    async def _handler(**kwargs):
        return "password=hunter2"

    ex = ToolExecutor(_registry(_handler), bus=bus)
    result = _run(ex.execute("probe", {}))
    assert result["status"] == "blocked_output"
    assert "Output guardrail" in result["error"]


def test_clean_call_succeeds_and_fires_post():
    """No veto → success; TOOL_POST consumer observed the result."""
    seen: list[ToolContext] = []

    class _Spy(MemoryHook):
        priority = HookPriority.OBSERVER

        async def on_tool_post(self, ctx: ToolContext):
            seen.append(ctx)
            return None

    bus = MemoryEventBus()
    bus.register(Guardrail(), EventType.TOOL_PRE, EventType.TOOL_POST)
    bus.register(_Spy(), EventType.TOOL_POST)

    async def _handler(**kwargs):
        return {"ok": True}

    ex = ToolExecutor(_registry(_handler), bus=bus)
    result = _run(ex.execute("probe", {"x": 1}))
    assert result["status"] == "success"
    assert result["output"] == {"ok": True}
    assert len(seen) == 1
    assert seen[0].result == {"ok": True}


# ── H2: TOOL_POST_FAIL fire on exception / timeout ────────────────────


def test_handler_exception_fires_post_fail_and_returns_error():
    fails: list[ToolContext] = []

    class _Spy(MemoryHook):
        async def on_tool_post_fail(self, ctx: ToolContext):
            fails.append(ctx)
            return None

    bus = MemoryEventBus()
    bus.register(_Spy(), EventType.TOOL_POST_FAIL)

    async def _handler(**kwargs):
        raise RuntimeError("boom")

    ex = ToolExecutor(_registry(_handler), bus=bus)
    result = _run(ex.execute("probe", {}))
    assert result["status"] == "error"
    assert "boom" in result["error"]
    assert len(fails) == 1
    assert isinstance(fails[0].error, Exception)


def test_handler_timeout_fires_post_fail_and_returns_timeout():
    fails: list[ToolContext] = []

    class _Spy(MemoryHook):
        async def on_tool_post_fail(self, ctx: ToolContext):
            fails.append(ctx)
            return None

    bus = MemoryEventBus()
    bus.register(_Spy(), EventType.TOOL_POST_FAIL)

    async def _handler(**kwargs):
        await asyncio.sleep(5)
        return "late"

    ex = ToolExecutor(_registry(_handler), bus=bus)
    result = _run(ex.execute("probe", {}, timeout=0.05))
    assert result["status"] == "timeout"
    assert len(fails) == 1


# ── Degradation: bus=None keeps the legacy test shape working ─────────


def test_bus_none_runs_handler_no_guardrail():
    """bus=None (the historical ``ToolExecutor(registry)`` shape) skips all
    firing and guardrail checks — tool runs unconditionally. Existing tests
    that don't depend on guardrail blocking stay green."""
    invoked = []

    async def _handler(**kwargs):
        invoked.append(True)
        return "ok"

    ex = ToolExecutor(_registry(_handler))  # no bus
    result = _run(ex.execute("probe", {"cmd": "rm -rf /anything"}))
    assert result["status"] == "success"
    assert invoked == [True]


# ── Red line: no hard-coded guardrail attribute ───────────────────────


def test_executor_has_no_hardcoded_guardrail_attribute():
    """ADR-3: the ``_guardrail`` attribute is gone — the decision now flows
    through the bus, not a direct ``self._guardrail.check`` call."""
    ex = ToolExecutor(ToolRegistry())
    assert not hasattr(ex, "_guardrail")
