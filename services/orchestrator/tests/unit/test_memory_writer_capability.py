"""MemoryWriterCapability 单测(P5 workflow 鸿沟解)。

核心断言:per-result emit(N 轮 tool_result = N 次沉淀,**不丢中间轮** ——
这正是 workflow 对抗验证发现 after_run 一次性触发的鸿沟)。+ env gate + ADR-7
不污染主路径 + retry/failed outcome 不误沉淀。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

from pydantic_ai import FunctionToolCallEvent, FunctionToolResultEvent, PartDeltaEvent
from pydantic_ai.messages import TextPartDelta, ToolCallPart, ToolReturnPart

from src.harness.capabilities.memory_writer_capability import MemoryWriterCapability
from src.memory.event_bus import EventType


class _StubBus:
    """记录所有 emit 调用(event, ctx)。"""

    def __init__(self) -> None:
        self.calls: list[tuple[Any, Any]] = []

    async def emit(self, event: Any, ctx: Any) -> Any:
        self.calls.append((event, ctx))
        return None


@dataclass
class _Ctx:
    """最小 RunContext stub(prompt + messages)。"""

    prompt: Any = "hello"
    messages: list = field(default_factory=list)


def _ts() -> datetime:
    return datetime(2026, 1, 1, tzinfo=timezone.utc)


def _tool_call(name: str, call_id: str) -> FunctionToolCallEvent:
    return FunctionToolCallEvent(part=ToolCallPart(tool_name=name, tool_call_id=call_id))


def _tool_result(name: str, call_id: str, content: str, outcome: str = "success") -> FunctionToolResultEvent:
    return FunctionToolResultEvent(
        part=ToolReturnPart(
            tool_name=name, content=content, tool_call_id=call_id,
            timestamp=_ts(), outcome=outcome,
        )
    )


def _delta(text: str) -> PartDeltaEvent:
    return PartDeltaEvent(index=0, delta=TextPartDelta(content_delta=text))


async def _drain(cap: MemoryWriterCapability, events: list) -> list:
    """跑 wrap_run_event_stream,收集 forwarded events。"""
    async def src():
        for e in events:
            yield e
    out = []
    async for e in cap.wrap_run_event_stream(_Ctx(), stream=src()):
        out.append(e)
    return out


def _turn_ends(bus: _StubBus) -> list:
    return [c for ev, c in bus.calls if ev == EventType.TURN_END]


def _tool_result_items(bus: _StubBus) -> list:
    return [c.tool_result_item for c in _turn_ends(bus) if getattr(c, "tool_result_item", None) is not None]


# ── P5 鸿沟解:每轮 tool_result 独立沉淀(N 轮 N 次,不丢中间轮)──────

def test_two_tool_rounds_each_sediment_independently() -> None:
    bus = _StubBus()
    cap = MemoryWriterCapability(memory_event_bus=bus, agent_id="a", session_id="s")
    events = [
        _tool_call("search", "c1"), _tool_result("search", "c1", "result-1"),
        _tool_call("read", "c2"), _tool_result("read", "c2", "result-2"),
        _delta("final answer"),
    ]
    forwarded = asyncio.run(_drain(cap, events))
    assert len(forwarded) == 5                       # 全 forward(主路径不阻塞)
    items = _tool_result_items(bus)
    assert len(items) == 2                           # 2 轮 = 2 次 tool_result 沉淀(不丢中间轮)
    assert "search" in items[0].content
    assert "read" in items[1].content
    # 每轮 tool_result 触发 INGEST(2)+ stream 耗尽 working 也 INGEST(1)= 3
    ingests = [c for ev, c in bus.calls if ev == EventType.INGEST]
    assert len(ingests) == 3


def test_turn_end_working_sedimented_on_stream_end() -> None:
    """stream 耗尽 → TURN_END(working)+ PRE_COMPRESS(用户轮四件套)。"""
    bus = _StubBus()
    cap = MemoryWriterCapability(memory_event_bus=bus, agent_id="a", session_id="s")
    asyncio.run(_drain(cap, [_delta("hello "), _delta("world")]))
    working = [c for c in _turn_ends(bus) if getattr(c, "working_item", None) is not None]
    assert len(working) == 1
    assert "User: hello" in working[0].working_item.content
    assert "hello world" in working[0].working_item.content     # response 从 PartDelta 累积
    assert any(ev == EventType.PRE_COMPRESS for ev, _ in bus.calls)


# ── env gate + ADR-7 不污染主路径 ─────────────────────────────────────

def test_no_bus_no_op_forward_only() -> None:
    cap = MemoryWriterCapability(memory_event_bus=None)
    events = [_tool_result("x", "c1", "r"), _delta("done")]
    assert asyncio.run(_drain(cap, events)) == events           # forward 不断,无 emit


def test_emit_failure_does_not_break_stream() -> None:
    """bus.emit raise → 主 stream 仍完整 forward(ADR-7)。"""
    class _BoomBus:
        async def emit(self, event, ctx):
            raise RuntimeError("bus down")
    cap = MemoryWriterCapability(memory_event_bus=_BoomBus(), agent_id="a", session_id="s")
    events = [_tool_result("x", "c1", "r"), _delta("done")]
    assert asyncio.run(_drain(cap, events)) == events           # 主路径不破


# ── 正确性:retry / failed outcome 不误沉淀 ──────────────────────────

def test_retry_prompt_not_sedimented() -> None:
    """ModelRetry 的 RetryPromptPart → 不当 tool_result 沉淀。"""
    bus = _StubBus()
    cap = MemoryWriterCapability(memory_event_bus=bus, agent_id="a", session_id="s")
    retry_part = SimpleNamespace(part_kind="retry-prompt", tool_call_id="c1", content="retry")
    asyncio.run(_drain(cap, [FunctionToolResultEvent(part=retry_part), _delta("ok")]))
    assert _tool_result_items(bus) == []


def test_failed_outcome_not_sedimented() -> None:
    """tool outcome=failed → 不沉淀(只 success 沉淀 tool_result)。"""
    bus = _StubBus()
    cap = MemoryWriterCapability(memory_event_bus=bus, agent_id="a", session_id="s")
    asyncio.run(_drain(cap, [_tool_result("x", "c1", "err", outcome="failed"), _delta("done")]))
    assert _tool_result_items(bus) == []


# ── defer_loading=False(写侧,主路径必须挂,非 recall 工具)────────────

def test_defer_loading_false() -> None:
    cap = MemoryWriterCapability(memory_event_bus=_StubBus())
    assert cap.defer_loading is False
