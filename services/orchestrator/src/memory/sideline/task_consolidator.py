"""TaskConsolidationAgent — task-post online consolidation [P3].

background_review style: after a task completes, LLM extracts key
decisions / pitfalls / tool patterns from the conversation, then writes
them back via :class:`BackwardWriter`'s three channels (confidence-routed).

Complements :class:`DreamerAgent` (periodic offline L2→L3) with task-post
online sedimentation. Fire-and-forget (caller wraps in asyncio.create_task),
2s timeout, failure degrades to a simple EPISODIC summary. Written
EPISODIC/SEMANTIC are ``origin=AGENT`` (BackwardWriter + degrade path),
so DreamerAgent can later promote them.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from src.memory.service import MemoryService
from src.memory.sideline.backward_writer import BackwardWriter
from src.memory.types import MemoryOrigin, MemoryScope, MemoryType

logger = logging.getLogger(__name__)

_EXTRACT_PROMPT = (
    "你是记忆巩固助手。分析以下任务对话,提取值得长期记住的经验(关键决策/踩坑/工具模式)。\n"
    "严格按格式输出:\nCONFIDENCE: <0-1 的置信度,越高越确定>\nCONTENT:\n"
    "<提取的经验,不超过150字>\n\n对话:\n{conversation}"
)


@dataclass
class ConsolidateResult:
    """Outcome of a consolidate_task call."""

    triggered: bool = False
    channel: str = ""
    written: bool = False
    memory_id: str = ""
    content: str = ""
    confidence: float = 0.0
    degraded: bool = False
    error: str = ""


class TaskConsolidationAgent:
    """Task-post online consolidation agent."""

    def __init__(
        self,
        memory_service: MemoryService,
        llm_client: Any | None = None,
        backward_writer: BackwardWriter | None = None,
    ) -> None:
        self._memory = memory_service
        self._llm = llm_client
        self._writer = backward_writer or BackwardWriter(memory_service, llm_client)

    async def consolidate_task(
        self,
        agent_id: str,
        session_id: str,
        messages: list[dict[str, Any]] | None = None,
        timeout: float | None = None,  # None → _state.SIDELLM_TIMEOUT
    ) -> ConsolidateResult:
        """Extract key experience from a completed task and write it back.

        Never raises — LLM timeout/failure degrades to a heuristic summary
        stored directly as EPISODIC (origin=AGENT).
        """
        result = ConsolidateResult(triggered=True)
        msgs = messages or []

        from src.services import _state
        if timeout is None:
            timeout = _state.SIDELLM_TIMEOUT
        try:
            content, confidence = await asyncio.wait_for(
                self._extract(msgs), timeout=timeout
            )
        except asyncio.TimeoutError:
            logger.warning(
                "TaskConsolidator LLM extract timed out (%.1fs) — degrading",
                timeout,
            )
            return await self._degrade(agent_id, session_id, msgs)
        except Exception as exc:
            logger.warning("TaskConsolidator extract failed: %s — degrading", exc)
            return await self._degrade(agent_id, session_id, msgs)

        result.content = content
        result.confidence = confidence
        try:
            wb = await self._writer.write(
                content=content,
                confidence=confidence,
                target=session_id,
                agent_id=agent_id,
            )
            result.channel = wb.channel.value
            result.written = wb.written
            result.memory_id = wb.memory_id
        except Exception as exc:
            logger.warning("TaskConsolidator write-back failed: %s", exc)
            result.error = str(exc)
        return result

    async def _extract(self, messages: list[dict[str, Any]]) -> tuple[str, float]:
        """LLM-extract key decisions/pitfalls/tool patterns + confidence.

        Falls back to a heuristic (last assistant message, confidence 0.4)
        when no LLM is configured or the call fails.
        """
        if self._llm is None or not messages:
            return self._heuristic(messages), 0.4

        conversation = self._format_messages(messages)
        prompt = _EXTRACT_PROMPT.format(conversation=conversation[:3000])
        # Let LLM failures propagate — consolidate_task catches them and
        # degrades to a heuristic EPISODIC summary.
        response = await self._llm.chat(
            [{"role": "user", "content": prompt}],
            max_tokens=300,
            temperature=0.3,
        )
        return self._parse_response(response or "")

    async def _degrade(
        self, agent_id: str, session_id: str, messages: list[dict[str, Any]]
    ) -> ConsolidateResult:
        """Fallback: store a simple EPISODIC summary directly (origin=AGENT)."""
        from src.services import _state
        _state.record_degrade("task_consolidator")
        summary = self._heuristic(messages)
        result = ConsolidateResult(
            triggered=True, degraded=True, content=summary, confidence=0.4
        )
        try:
            ref = await self._memory.store(
                content=summary,
                agent_id=agent_id,
                session_id=session_id,
                memory_type=MemoryType.EPISODIC,
                scope=MemoryScope.AGENT,
                importance=0.4,
                origin=MemoryOrigin.AGENT,
                metadata={"source": "task_consolidator", "degraded": True},
            )
            result.written = True
            result.memory_id = ref.id
            result.channel = "slow(degraded)"
        except Exception as exc:
            logger.warning("TaskConsolidator degrade store failed: %s", exc)
            result.error = str(exc)
        return result

    @staticmethod
    def _format_messages(messages: list[dict[str, Any]]) -> str:
        return "\n".join(
            f"{m.get('role', '?')}: {m.get('content', '')}" for m in messages[-20:]
        )

    @staticmethod
    def _heuristic(messages: list[dict[str, Any]]) -> str:
        for m in reversed(messages):
            if m.get("role") == "assistant" and m.get("content"):
                return f"[Task summary] {m['content'][:200]}"
        return "[Task summary] (empty)"

    @staticmethod
    def _parse_response(text: str) -> tuple[str, float]:
        """Parse 'CONFIDENCE: x\\nCONTENT:\\n...' from LLM output."""
        confidence = 0.5
        content = text.strip()
        lines = text.splitlines()
        for i, line in enumerate(lines):
            low = line.lower()
            if low.startswith("confidence:"):
                try:
                    confidence = float(low.split(":", 1)[1].strip())
                except ValueError:
                    pass
            elif low.startswith("content:"):
                content = "\n".join(lines[i + 1:]).strip() or text.strip()
                break
        return content[:500], max(0.0, min(1.0, confidence))
