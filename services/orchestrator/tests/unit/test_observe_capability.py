"""P5 ObserveCapability 单测:wrap 映射 AgentStreamEvent → ObserveEvent 闭环。

stub emitter(记录 emit)+ fake event stream,验 tick_started→tool_call→tool_result→
tick_completed(success)序列 + token_delta + error 闭环 + ADR-7(emit 异常不污染)+ outermost。
"""

from __future__ import annotations

import asyncio

import pytest
from pydantic_ai import (
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    PartDeltaEvent,
)
from pydantic_ai.messages import TextPartDelta, ToolCallPart, ToolReturnPart

from src.harness.capabilities import ObserveCapability


class _StubEmitter:
    def __init__(self, fail_once_at: int | None = None) -> None:
        self.emitted: list[dict] = []
        self.fail_once_at = fail_once_at  # 第 N 次 emit 抛一次(测 ADR-7)
        self._failed = False

    async def emit(self, ev: dict) -> None:
        if (self.fail_once_at is not None and not self._failed
                and len(self.emitted) == self.fail_once_at):
            self._failed = True
            raise RuntimeError("observe down")
        self.emitted.append(ev)


async def _consume(gen) -> None:
    async for _ in gen:
        pass


def _types(emitted: list[dict]) -> list[str]:
    return [e["event_type"] for e in emitted]


def test_observe_maps_tool_call_and_result_to_closed_tick() -> None:
    em = _StubEmitter()
    cap = ObserveCapability(emitter=em, harness_id="h1", session_id="s1")

    async def stream():
        yield FunctionToolCallEvent(ToolCallPart("search", {"q": "x"}, "cid1"))
        yield FunctionToolResultEvent(ToolReturnPart(
            tool_call_id="cid1", tool_name="search", content="found"))

    asyncio.run(_consume(cap.wrap_run_event_stream(ctx=None, stream=stream())))

    assert _types(em.emitted) == [
        "tick_started", "tool_call", "tool_result", "tick_completed",
    ]
    completed = em.emitted[-1]["data"]
    assert completed["status"] == "success"
    assert completed["tool_count"] == 1
    # harness_type 全程 agent-os-v2
    assert all(e["harness_type"] == "agent-os-v2" for e in em.emitted)


def test_observe_maps_token_delta() -> None:
    em = _StubEmitter()
    cap = ObserveCapability(emitter=em, harness_id="h1", session_id="s1")

    async def stream():
        yield PartDeltaEvent(index=0, delta=TextPartDelta(content_delta="hel"))
        yield PartDeltaEvent(index=0, delta=TextPartDelta(content_delta="lo"))

    asyncio.run(_consume(cap.wrap_run_event_stream(ctx=None, stream=stream())))
    deltas = [e for e in em.emitted if e["event_type"] == "token_delta"]
    assert [d["data"]["delta_text"] for d in deltas] == ["hel", "lo"]
    # 首 tick_started + 末 tick_completed success
    assert em.emitted[0]["event_type"] == "tick_started"
    assert em.emitted[-1]["data"]["status"] == "success"


def test_observe_emitter_failure_does_not_pollute_main_path() -> None:
    """ADR-7:emitter.emit 抛异常,wrap 仍 forward 全部事件 + 正常闭环。"""
    em = _StubEmitter(fail_once_at=1)  # 第 2 次 emit(tool_call)抛
    cap = ObserveCapability(emitter=em, harness_id="h1", session_id="s1")

    forwarded = []

    async def stream():
        yield FunctionToolCallEvent(ToolCallPart("t", {}, "c1"))

    async def drive() -> None:
        async for e in cap.wrap_run_event_stream(ctx=None, stream=stream()):
            forwarded.append(e)  # 主路径仍收到 event

    asyncio.run(drive())
    # 主路径 forward 未断(forwarded 有 event);闭环仍发(emit 异常被 _emit 吞)
    assert len(forwarded) == 1
    assert em.emitted[-1]["event_type"] == "tick_completed"


def test_observe_outermost_and_no_emitter_passthrough() -> None:
    cap = ObserveCapability(emitter=None)
    assert cap.get_ordering().position == "outermost"
    # 无 emitter → 纯 forward(不产生 observe 事件)
    async def stream():
        yield FunctionToolCallEvent(ToolCallPart("t", {}, "c1"))
    forwarded = []
    asyncio.run(_consume_async(cap, stream(), forwarded))


def _consume_async(cap, stream, forwarded):
    async def _d():
        async for e in cap.wrap_run_event_stream(ctx=None, stream=stream):
            forwarded.append(e)
    return _d()


def test_observe_skips_retry_prompt() -> None:
    """ModelRetry 场景的 RetryPromptPart 不上报(避免 retry 误报成 success tool_result)。"""
    from pydantic_ai.messages import RetryPromptPart

    em = _StubEmitter()
    cap = ObserveCapability(emitter=em, harness_id="h", session_id="s")

    async def stream():
        yield FunctionToolResultEvent(RetryPromptPart(content="retry me"))

    asyncio.run(_consume(cap.wrap_run_event_stream(ctx=None, stream=stream())))
    # 无 tool_result 上报(只 tick_started + tick_completed)
    assert not any(e["event_type"] == "tool_result" for e in em.emitted)


def test_observe_tool_result_denied_outcome_reports_error() -> None:
    """ToolReturnPart.outcome=denied/failed → tool_result error(非静默 success)。"""
    em = _StubEmitter()
    cap = ObserveCapability(emitter=em, harness_id="h", session_id="s")

    class _DeniedPart:
        part_kind = "return"
        tool_call_id = "c1"
        content = "nope"
        outcome = "denied"

    async def stream():
        yield FunctionToolResultEvent(_DeniedPart())

    asyncio.run(_consume(cap.wrap_run_event_stream(ctx=None, stream=stream())))
    tr = [e for e in em.emitted if e["event_type"] == "tool_result"][0]
    assert tr["data"]["error"]                       # denied → error 非空
    assert "denied" in tr["data"]["error"]
