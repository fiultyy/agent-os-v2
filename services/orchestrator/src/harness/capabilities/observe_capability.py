"""P5 ObserveCapability — native run event stream → observe-service.

ADR: docs/adr/pydantic-ai-v2-adoption.md。把 pydantic-ai 2.0 的 AgentStreamEvent
(FunctionToolCall/FunctionToolResult/PartDelta)映射成 v2 ObserveEvent(tool_call/
tool_result/token_delta),经 ObserveEmitter(harness/emit.py)推 observe。tick_started
在 stream 入口合成(2.0 无 run-start 事件),tick_completed 在 stream 耗尽发(success),
异常分支补 error tick 再传播。

⚠️ 只覆盖 native in-process Agent 路径(ADR)。外部 harness(claw/claude-code)的事件源
在 claude.py/openclaw.py 自己解析,不经此 capability(对抗验证 hard-blocking 结论)。

ADR-7 fire-and-forget:emitter.emit 异常绝不污染 Agent.run 主路径(_emit try/except 包死)。
position=outermost:保证最先/最后看到每个事件(完整 tick 闭环不被内层 capability 短路)。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from pydantic_ai import (
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    PartDeltaEvent,
)
from pydantic_ai.capabilities import AbstractCapability, CapabilityOrdering
from pydantic_ai.messages import TextPartDelta

from src.harness.events import (
    tick_completed,
    tick_started,
    token_delta,
    tool_call,
    tool_result,
)

HARNESS_TYPE = "agent-os-v2"  # native in-process agent = agent-os-v2 harness


@dataclass
class ObserveCapability(AbstractCapability[Any]):
    """native run → observe 事件流映射(outermost,fire-and-forget)。"""

    id: str = "observe"
    description: str = "Stream native run events → observe-service (tick/tool/token)"
    defer_loading: bool = False
    emitter: Any = None  # ObserveEmitter(harness/emit.py)
    harness_id: str = ""
    session_id: str = ""

    def get_ordering(self) -> CapabilityOrdering:
        return CapabilityOrdering(position="outermost")

    async def wrap_run_event_stream(self, ctx, *, stream):
        if self.emitter is None:
            async for event in stream:
                yield event
            return

        tick_id = str(uuid.uuid4())
        tool_count = 0
        await self._emit(
            tick_started(HARNESS_TYPE, self.harness_id, self.session_id, tick_id, "[native run]")
        )
        try:
            async for event in stream:
                yield event  # forward 到主路径(always)
                if isinstance(event, FunctionToolCallEvent):
                    tool_count += 1
                    part = event.part
                    args = part.args if isinstance(part.args, dict) else {}
                    await self._emit(tool_call(
                        HARNESS_TYPE, self.harness_id, self.session_id, tick_id,
                        tool_name=part.tool_name, arguments=args,
                        call_id=part.tool_call_id,
                    ))
                elif isinstance(event, FunctionToolResultEvent):
                    part = event.part
                    # RetryPromptPart(ModelRetry 场景)非真 result — 不上报,避免
                    # 把 retry 消息误报成成功 tool_result 污染 observe
                    if getattr(part, "part_kind", None) == "retry-prompt":
                        pass
                    else:
                        # ToolReturnPart.outcome: success | failed | denied | interrupted
                        outcome = getattr(part, "outcome", "success")
                        ok = outcome == "success"
                        await self._emit(tool_result(
                            HARNESS_TYPE, self.harness_id, self.session_id, tick_id,
                            call_id=getattr(part, "tool_call_id", ""),
                            result=part.content if ok else None,
                            error="" if ok else f"tool outcome: {outcome}",
                        ))
                elif isinstance(event, PartDeltaEvent) and isinstance(event.delta, TextPartDelta):
                    await self._emit(token_delta(
                        HARNESS_TYPE, self.harness_id, self.session_id, tick_id,
                        delta_text=event.delta.content_delta,
                    ))
            # stream 正常耗尽 = success 闭环
            await self._emit(tick_completed(
                HARNESS_TYPE, self.harness_id, self.session_id, tick_id,
                status="success", tool_count=tool_count,
            ))
        except Exception:
            # 主路径异常:补 error tick 闭环(observe 不悬),再传播(不吞主异常)
            await self._emit(tick_completed(
                HARNESS_TYPE, self.harness_id, self.session_id, tick_id,
                status="error", response="native run failed",
            ))
            raise

    async def _emit(self, event: dict) -> None:
        """ADR-7:observe emit 失败绝不污染 Agent.run 主路径。"""
        try:
            await self.emitter.emit(event)
        except Exception:
            pass
