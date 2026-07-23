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
    # ADR-1: semantic agent_id owning this turn. For a consumed A2A agent this
    # is the TARGET id (set by assemble_capabilities from spec_id), NOT the
    # caller's — so observe can tell whose turn each event belongs to.
    agent_id: str = ""

    def get_ordering(self) -> CapabilityOrdering:
        return CapabilityOrdering(position="outermost")

    async def wrap_run_event_stream(self, ctx, *, stream):
        if self.emitter is None:
            async for event in stream:
                yield event
            return

        tick_id = str(uuid.uuid4())
        tool_count = 0
        started = False  # 延迟到首个有内容 event 才 emit tick_started(见 async for)
        # 用户消息优先(ctx.prompt);占位 [native run] 仅在 prompt 不可得时(避免 TUI 把
        # 占位当用户消息渲染 → 内容跟 cc/oc harness 不一致)。
        prompt = getattr(ctx, "prompt", None)
        user_msg = prompt if isinstance(prompt, str) and prompt.strip() else "[native run]"
        try:
            async for event in stream:
                # 延迟 tick_started 到首个有内容 event(token/tool):pydantic-ai graph
                # 某 wrap 产生空/非内容 stream,emit 空 tick 会让 TUI 重复显示同一条
                # 用户消息(两个 tick_id 各一 user cell)。
                if not started:
                    has_content = isinstance(event, (FunctionToolCallEvent, FunctionToolResultEvent)) or (
                        isinstance(event, PartDeltaEvent) and isinstance(event.delta, TextPartDelta)
                    )
                    if has_content:
                        await self._emit(
                            tick_started(HARNESS_TYPE, self.harness_id, self.session_id, tick_id, user_msg,
                                         agent_id=self.agent_id)
                        )
                        started = True
                yield event  # forward 到主路径(always)
                if isinstance(event, FunctionToolCallEvent):
                    tool_count += 1
                    part = event.part
                    args = part.args if isinstance(part.args, dict) else {}
                    await self._emit(tool_call(
                        HARNESS_TYPE, self.harness_id, self.session_id, tick_id,
                        tool_name=part.tool_name, arguments=args,
                        call_id=part.tool_call_id, agent_id=self.agent_id,
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
                            agent_id=self.agent_id,
                        ))
                elif isinstance(event, PartDeltaEvent) and isinstance(event.delta, TextPartDelta):
                    await self._emit(token_delta(
                        HARNESS_TYPE, self.harness_id, self.session_id, tick_id,
                        delta_text=event.delta.content_delta, agent_id=self.agent_id,
                    ))
            # stream 正常耗尽 = success 闭环(仅当 started:空/非内容 wrap 不 emit tick)
            if started:
                await self._emit(tick_completed(
                    HARNESS_TYPE, self.harness_id, self.session_id, tick_id,
                    status="success", tool_count=tool_count, agent_id=self.agent_id,
                ))
        except Exception:
            # 主路径异常:补 error tick 闭环(仅当 started),再传播(不吞主异常)
            if started:
                await self._emit(tick_completed(
                    HARNESS_TYPE, self.harness_id, self.session_id, tick_id,
                    status="error", response="native run failed", agent_id=self.agent_id,
                ))
            raise

    async def _emit(self, event: dict) -> None:
        """ADR-7:observe emit 失败绝不污染 Agent.run 主路径。"""
        try:
            await self.emitter.emit(event)
        except Exception:
            pass
