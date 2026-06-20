"""ConsolidatorAgent — episodic → semantic LLM-driven consolidation [Step ②].

Side agent (OBSERVER-priority hook) for ``EventType.CONSOLIDATE`` /
session_end. Complements :class:`TaskConsolidationAgent` (task-post
experience sedimentation) and :meth:`SessionOperations.reflect`
(deterministic Jaccard merge): here an LLM reads the most recent
``origin=AGENT`` EPISODIC memories, emits coherent merged SEMANTIC
summaries plus detected contradictions, and the merged source items are
archived (sedimented).

P0 red-line: candidate selection reuses the exact ``reflect`` filter
(``MemoryFilter(origin=AGENT, memory_type=EPISODIC)``) so FOREGROUND
memories are never touched. Degradation: any LLM/parse failure falls back
to ``service.reflect`` (the deterministic Jaccard path) — never raises.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any

from src.memory.hooks import (
    ConsolidateContext,
    HookPriority,
    MemoryHook,
    SessionContext,
)
from src.memory.service import MemoryService
from src.memory.types import MemoryFilter, MemoryOrigin, MemoryType

logger = logging.getLogger(__name__)


_VALID_IDENTITY_CATEGORIES = {"IDENTITY", "GOAL", "TRAIT", "KNOWLEDGE", "NONE"}


_CONSOLIDATE_PROMPT = (
    "你是记忆巩固助手。下面是某 agent 最近的若干条 episodic 经验片段。\n"
    "请理解它们的语义后,把含义相关、可以合并成一条更完整认知的片段融合为连贯叙述"
    "(不要用 \\n---\\n 之类分隔符机械拼接,要重写成一段通顺的话),"
    "并标注该合并条目的身份类别(identity_category)。\n"
    "同时指出互相矛盾的片段对。\n\n"
    "严格只输出如下 JSON(不要任何额外文字、不要 markdown 代码块):\n"
    "{{\n"
    '  "merged": [\n'
    "    {{\n"
    '      "summary": "<合并后的连贯叙述>",\n'
    '      "source_ids": ["<被合并的片段id>", "..."],\n'
    '      "importance": <0-1 之间的重要性,越高越核心>,\n'
    '      "identity_category": "IDENTITY | GOAL | TRAIT | KNOWLEDGE | NONE"\n'
    "    }}\n"
    "  ],\n"
    '  "contradictions": [\n'
    '    {{ "ids": ["<冲突片段id>", "..."], "note": "<冲突说明>" }}\n'
    "  ]\n"
    "}}\n\n"
    "规则:\n"
    "- source_ids 必须来自下方给出的片段 id,且至少 2 条才形成合并(单条无需合并)。\n"
    "- importance 是 0 到 1 之间的数。\n"
    "- identity_category 只能取上述五个枚举值之一。\n"
    "- contradictions 可为空数组。\n\n"
    "片段列表(JSON,字段 id/content/importance):\n"
    "{candidates}"
)


@dataclass
class ConsolidatorResult:
    """Outcome of a :meth:`ConsolidatorAgent.consolidate` call.

    Mirrors the unified ``SideAgentResult`` shape (alias of
    :class:`ConsolidateResult`) so callers and the bus treat every side
    agent uniformly.
    """

    triggered: bool = False
    written: bool = False
    degraded: bool = False
    merged_count: int = 0
    contradiction_count: int = 0
    semantic_ids: list[str] = field(default_factory=list)
    archived_ids: list[str] = field(default_factory=list)
    error: str = ""


class ConsolidatorAgent:
    """Episodic → semantic LLM consolidation agent.

    Never raises — LLM timeout / parse failure degrades to the
    deterministic :meth:`MemoryService.reflect` (Jaccard) path.
    """

    def __init__(
        self,
        memory_service: MemoryService,
        llm_client: Any | None = None,
    ) -> None:
        self._memory = memory_service
        self._llm = llm_client

    async def consolidate(
        self,
        agent_id: str,
        trigger: str = "periodic",
        top_k: int = 20,
        timeout: float = 8.0,
    ) -> ConsolidatorResult:
        """Consolidate recent agent-origin EPISODIC memories into SEMANTIC.

        Args:
            agent_id: The agent whose memories to consolidate.
            trigger: Reason for the consolidation (periodic / session_end).
            top_k: How many recent episodic items to feed the LLM.
            timeout: Max seconds for the LLM call before degrading.

        Returns:
            A :class:`ConsolidatorResult`. ``triggered=False`` when there
            are no candidate episodic memories; ``degraded=True`` when the
            LLM path failed and the deterministic ``reflect`` fallback ran.
        """
        # P0 provenance: identical filter to SessionOperations.reflect —
        # only agent-self-sedimented episodic items, FOREGROUND untouched.
        f = MemoryFilter(
            agent_id=agent_id,
            memory_type=MemoryType.EPISODIC,
            origin=MemoryOrigin.AGENT,
        )
        candidates = (await self._memory._store.search(f))[:top_k]

        if not candidates:
            return ConsolidatorResult(triggered=False)

        result = ConsolidatorResult(triggered=True)

        try:
            payload = await asyncio.wait_for(
                self._consolidate_llm(candidates), timeout=timeout
            )
        except asyncio.TimeoutError:
            logger.warning(
                "ConsolidatorAgent LLM timed out (%.1fs) — degrading to reflect",
                timeout,
            )
            return await self._degrade(agent_id, trigger, top_k)
        except Exception as exc:
            logger.warning("ConsolidatorAgent LLM failed: %s — degrading", exc)
            return await self._degrade(agent_id, trigger, top_k)

        try:
            await self._parse_consolidate(
                result=result,
                payload=payload,
                candidates=candidates,
                agent_id=agent_id,
                trigger=trigger,
            )
            result.written = result.merged_count > 0
        except Exception as exc:
            logger.warning("ConsolidatorAgent parse/write failed: %s — degrading", exc)
            return await self._degrade(agent_id, trigger, top_k)

        return result

    # ── LLM consolidation ──────────────────────────────────────────

    async def _consolidate_llm(self, candidates: list[Any]) -> dict[str, Any]:
        """Call the LLM with candidate content and parse JSON output.

        Propagates failures (no LLM, bad JSON) so :meth:`consolidate` can
        degrade uniformly.
        """
        if self._llm is None:
            raise RuntimeError("no LLM client configured")

        candidate_payload = [
            {"id": c.id, "content": c.content, "importance": c.importance}
            for c in candidates
        ]
        prompt = _CONSOLIDATE_PROMPT.format(
            candidates=json.dumps(candidate_payload, ensure_ascii=False)[:4000]
        )
        response = await self._llm.chat(
            [{"role": "user", "content": prompt}],
            max_tokens=800,
            temperature=0.3,
        )
        return self._extract_json(response or "")

    @staticmethod
    def _extract_json(response: str) -> dict[str, Any]:
        """Best-effort JSON extraction from an LLM response.

        Strips markdown code fences and trailing prose; falls back to
        locating the outermost ``{...}`` block.
        """
        text = response.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            text = "\n".join(lines).strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(text[start : end + 1])
        raise ValueError("no JSON object in LLM response")

    async def _parse_consolidate(
        self,
        result: ConsolidatorResult,
        payload: dict[str, Any],
        candidates: list[Any],
        agent_id: str,
        trigger: str,
    ) -> None:
        """Persist merged SEMANTIC items and archive their source episodics.

        Each merged entry needs a coherent ``summary`` and ≥2 valid
        ``source_ids`` (single fragments need no merging). Failures of an
        individual entry or source-archive are logged and skipped — never
        fatal to the whole pass.
        """
        valid_ids = {c.id for c in candidates}
        merged_list = payload.get("merged") or []

        for entry in merged_list:
            if not isinstance(entry, dict):
                continue
            summary = (entry.get("summary") or "").strip()
            source_ids = [
                sid for sid in (entry.get("source_ids") or []) if sid in valid_ids
            ]
            if not summary or len(source_ids) < 2:
                continue

            importance = _clamp(
                _coerce_float(entry.get("importance"), default=0.5), 0.0, 1.0
            )
            identity_category = _normalise_identity(entry.get("identity_category"))

            try:
                ref = await self._memory.store(
                    content=summary,
                    agent_id=agent_id,
                    memory_type=MemoryType.SEMANTIC,
                    importance=importance,
                    origin=MemoryOrigin.AGENT,
                    metadata={
                        "source_ids": source_ids,
                        "merged_count": len(source_ids),
                        "consolidation_trigger": trigger,
                        "identity_category": identity_category,
                    },
                )
            except Exception as exc:
                logger.warning("ConsolidatorAgent store merged failed: %s", exc)
                continue

            result.semantic_ids.append(ref.id)
            result.merged_count += 1

            for sid in source_ids:
                try:
                    updated = await self._memory.update(memory_id=sid, archived=True)
                except Exception as exc:
                    logger.warning(
                        "ConsolidatorAgent archive source %s failed: %s", sid, exc
                    )
                    continue
                if updated is not None:
                    result.archived_ids.append(sid)

        contradictions = payload.get("contradictions") or []
        if isinstance(contradictions, list):
            result.contradiction_count = sum(
                1 for c in contradictions if isinstance(c, dict) and (c.get("ids") or [])
            )

    # ── Degradation ───────────────────────────────────────────────

    async def _degrade(
        self, agent_id: str, trigger: str, top_k: int
    ) -> ConsolidatorResult:
        """Deterministic fallback: delegate to ``service.reflect`` (Jaccard)."""
        result = ConsolidatorResult(triggered=True, degraded=True)
        try:
            refs = await self._memory.reflect(
                agent_id=agent_id, trigger=trigger, top_k=top_k
            )
        except Exception as exc:
            logger.warning("ConsolidatorAgent reflect fallback failed: %s", exc)
            result.error = str(exc)
            return result
        result.semantic_ids = [r.id for r in refs]
        result.merged_count = len(refs)
        result.written = result.merged_count > 0
        return result


class ConsolidatorHook(MemoryHook):
    """OBSERVER-priority hook wiring :class:`ConsolidatorAgent` to the bus.

    Register explicitly for ``EventType.CONSOLIDATE`` (and/or
    ``EventType.SESSION_END``); the default all-events mount is disabled by
    the Step0 contract.
    """

    priority = HookPriority.OBSERVER

    def __init__(self, agent: ConsolidatorAgent) -> None:
        self._agent = agent

    async def on_consolidate(self, ctx: ConsolidateContext) -> ConsolidatorResult:
        return await self._agent.consolidate(
            agent_id=ctx.agent_id,
            trigger=ctx.trigger,
            top_k=ctx.top_k,
        )

    async def on_session_end(self, ctx: SessionContext) -> ConsolidatorResult:
        return await self._agent.consolidate(
            agent_id=ctx.agent_id, trigger="session_end"
        )


# ── Helpers ───────────────────────────────────────────────────────


def _coerce_float(value: Any, default: float = 0.5) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _normalise_identity(value: Any) -> str:
    if isinstance(value, str):
        v = value.strip().upper()
        if v in _VALID_IDENTITY_CATEGORIES:
            return v
    return "NONE"
