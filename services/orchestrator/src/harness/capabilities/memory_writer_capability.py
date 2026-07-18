"""P5 MemoryWriterCapability — native Agent.run 记忆沉淀(写侧,自动)。

ADR: docs/adr/pydantic-ai-v2-adoption.md(P5 + workflow 对抗验证 must_defer 解)。

承接老 chat.py 的记忆副作用:
  - ``_node_llm`` first_round:TURN_END(working User/Assistant)+ INGEST + KG + PRE_COMPRESS
  - ``_node_tool`` 每轮:TURN_END(tool_result_item)+ INGEST

与 ``MemoryCapability``(读侧 recall 工具,模型主动 load)分离:本 capability 不暴露 tool,
纯 ``wrap_run_event_stream`` 生命周期 hook → 不破 R1(模型无法 load 触发写)。

**时序(workflow P5 语义鸿沟的解)**:用 ``wrap_run_event_stream`` 而非 ``after_run``:
  - 每轮 ``FunctionToolResultEvent`` → 立即 sediment tool_result(N 轮 = N 次沉淀,**不丢中间轮**;
    ``after_run`` 一次性触发会丢中间轮 tool_result —— workflow 对抗验证发现的鸿沟)
  - stream 耗尽(用户轮结束)→ sediment working 四件套(对应 ``_node_llm`` first_round)

env gate:``memory_event_bus`` / ``knowledge_graph`` 为 None → no-op。ADR-7:记忆写绝不污染
Agent.run 主路径(全 try/except + fire-and-forget)。``position="inner"``(不抢 outermost,
让 observe 包外层做完整 tick 闭环)。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from pydantic_ai import (
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    PartDeltaEvent,
)
from pydantic_ai.capabilities import AbstractCapability, CapabilityOrdering
from pydantic_ai.messages import TextPartDelta

from src.memory import MemoryScope, MemoryType
from src.memory.event_bus import EventType
from src.memory.hooks import CompressContext, IngestContext, TurnContext
from src.memory.types import MemoryItem, MemoryOrigin

logger = logging.getLogger(__name__)

_PREVIEW = 200  # tool result 截断长度(对齐老 _node_tool result_preview)


@dataclass
class MemoryWriterCapability(AbstractCapability[Any]):
    """native run 记忆沉淀(写侧,自动 fire-and-forget)。"""

    id: str = "memory_writer"
    description: str = "Auto-sediment turn memory (TURN_END/INGEST/KG/PRE_COMPRESS)"
    defer_loading: bool = False  # 写侧,主路径必须挂(非 recall 工具,模型不 load)
    memory_event_bus: Any = None  # _state.memory_event_bus(None → 全 no-op)
    knowledge_graph: Any = None  # _state.knowledge_graph(None → KG extract no-op)
    agent_id: str = ""
    session_id: str = ""

    def get_ordering(self) -> CapabilityOrdering:
        return CapabilityOrdering(position="inner")  # observe 包外层

    async def wrap_run_event_stream(self, ctx, *, stream):
        if self.memory_event_bus is None:
            async for event in stream:
                yield event
            return

        user_prompt = ctx.prompt if isinstance(ctx.prompt, str) else ""
        response_parts: list[str] = []
        current_tool = ""

        async for event in stream:
            yield event  # forward always(主路径不阻塞)
            if isinstance(event, FunctionToolCallEvent):
                current_tool = event.part.tool_name
            elif isinstance(event, FunctionToolResultEvent):
                part = event.part
                # RetryPromptPart(ModelRetry)非真 result — 不沉淀(对齐 ObserveCapability)
                if getattr(part, "part_kind", None) != "retry-prompt":
                    outcome = getattr(part, "outcome", "success")
                    if outcome == "success":
                        await self._sediment_tool_result(
                            current_tool or getattr(part, "tool_name", "tool"),
                            str(part.content),
                        )
            elif isinstance(event, PartDeltaEvent) and isinstance(event.delta, TextPartDelta):
                response_parts.append(event.delta.content_delta)

        # stream 耗尽 = 用户轮结束 → working 四件套
        await self._sediment_turn_end(user_prompt, "".join(response_parts), ctx.messages)

    # ── 沉淀 helpers(全 try/except + env gate,绝不污染主路径)──────────

    async def _sediment_tool_result(self, tool_name: str, result: str) -> None:
        """每轮 tool 结果 → TURN_END(tool_result_item)+ INGEST。对应 _node_tool。"""
        try:
            bus = self.memory_event_bus
            if bus is None:
                return
            preview = result[:_PREVIEW]
            item = MemoryItem(
                content=f"Tool {tool_name} result: {preview}",
                agent_id=self.agent_id,
                session_id=self.session_id,
                memory_type=MemoryType.WORKING,
                scope=MemoryScope.AGENT,
                metadata={"safety_deadline": True, "tool_result": True},
            )
            await bus.emit(
                EventType.TURN_END,
                TurnContext(
                    agent_id=self.agent_id,
                    session_id=self.session_id,
                    tool_result_item=item,
                ),
            )
            self._fire_ingest(item.id, item.content)
        except Exception:
            logger.warning("MemoryWriter tool_result sediment failed (env gate no-op)", exc_info=True)

    async def _sediment_turn_end(self, user_prompt: str, response: str, messages: list) -> None:
        """用户轮结束 → TURN_END(working)+ INGEST + KG + PRE_COMPRESS。对应 _node_llm first_round。"""
        try:
            bus = self.memory_event_bus
            if bus is None:
                return
            working = MemoryItem(
                content=f"User: {user_prompt}\nAssistant: {response}",
                agent_id=self.agent_id,
                session_id=self.session_id,
                memory_type=MemoryType.WORKING,
                scope=MemoryScope.AGENT,
            )
            await bus.emit(
                EventType.TURN_END,
                TurnContext(
                    agent_id=self.agent_id,
                    session_id=self.session_id,
                    working_item=working,
                ),
            )
            self._fire_ingest(working.id, working.content)
            self._fire_kg(user_prompt, response)
            # PRE_COMPRESS:await(对称 _node_llm);summary_ids 不回填(capability 无 state 字段,
            # 压缩副作用已触发是核心,ids 回填属 state 观测,可接受损失)
            await bus.emit(
                EventType.PRE_COMPRESS,
                CompressContext(
                    agent_id=self.agent_id,
                    session_id=self.session_id,
                    accessor_id=self.agent_id,
                    messages=list(messages),
                ),
            )
        except Exception:
            logger.warning("MemoryWriter turn_end sediment failed (env gate no-op)", exc_info=True)

    def _fire_ingest(self, memory_id: str, content: str) -> None:
        """fire-and-forget INGEST(IngestorAgent;hook 未注册 → emit 返 None,no-op)。"""
        try:
            bus = self.memory_event_bus
            if bus is None:
                return
            ctx = IngestContext(
                memory_id=memory_id,
                content=content,
                agent_id=self.agent_id,
                session_id=self.session_id,
                origin=MemoryOrigin.AGENT.value,
            )
            asyncio.create_task(bus.emit(EventType.INGEST, ctx))
        except Exception:
            logger.warning("MemoryWriter INGEST fire failed (no-op)", exc_info=True)

    def _fire_kg(self, user_prompt: str, response: str) -> None:
        """fire-and-forget KG extract(entity/relation;kg None → no-op)。"""
        try:
            kg = self.knowledge_graph
            if kg is None or not hasattr(kg, "extract_and_ingest"):
                return
            text = f"User: {user_prompt}\nAssistant: {response}"
            asyncio.create_task(
                asyncio.to_thread(
                    kg.extract_and_ingest, text=text, memory_id=self.session_id,
                )
            )
        except Exception:
            logger.warning("MemoryWriter KG fire failed (no-op)", exc_info=True)
