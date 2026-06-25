"""IngestorAgent — ① memory pipeline side agent [Part 1 Step 1].

LLM semantic ingestion: raw memory text → KG entities/relations + five
dimensional importance + ``identity_category`` tag (lays data for the
Chapter 6 identity-recall closure).

Mirrors :class:`TaskConsolidationAgent` skeleton (``asyncio.wait_for`` +
``_extract`` / ``_degrade`` / ``_parse_response``). The whole call never
raises — LLM timeout / failure degrades to the regex KG extractor
(:meth:`KnowledgeGraph.extract_and_ingest`) plus the deterministic scorer.

P0 red line (highest priority): :meth:`ingest` returns early when
``origin == FOREGROUND``. Side agents only enrich AGENT memories; user /
foreground memories are never modified.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from src.memory.hooks import HookPriority, IngestContext, MemoryHook
from src.memory.knowledge_graph import Entity, KnowledgeGraph, Relation
from src.memory.scorer import ImportanceScorer
from src.memory.service import MemoryService
from src.memory.types import MemoryItem, MemoryOrigin

logger = logging.getLogger(__name__)

# Allowed identity_category tag values (Chapter 6.1). NONE is the default
# when the LLM does not recognise an identity-bearing memory.
# importance 卫生:失忆/无信息回复模式(协作需求 —— 失智回复不该排第一污染召回)。
# 通用否定/失忆语义,精确不误伤("我找到了 Logseq 的记录" 不命中)。命中 →
# importance cap 0.3 + metadata.low_info_reply=True。
_LOW_INFO_PATTERNS = re.compile(
    r"没有找到|没找到|找不到|未找到|没有相关|搜不到|查不到|"
    r"不记得|不知道|不清楚|不了解|无法确定|没有印象|无法访问|"
    r"没有.{0,8}(?:记录|对话|历史|聊天|数据|信息)|"
    r"全新的工作区|新会话|刚刚上线|刚上线|还没有.{0,8}(?:记录|历史|数据|聊过)|"
    r"MEMORY\.md.{0,6}(?:是空|为空|没有)|memory(?:文件夹|目录|文件).{0,6}(?:没有|为空|是空)|"
    r"什么都没(?:有|记得)|没有任何(?:记录|历史|对话)|我现在什么都不记得"
)

_IDENTITY_CATEGORIES = {"IDENTITY", "GOAL", "TRAIT", "KNOWLEDGE", "NONE"}

# Allowed entity ``type`` values (Chapter 6.1 KG entity-type extension).
_ENTITY_TYPES = {
    "identity",
    "goal",
    "trait",
    "capability",
    "knowledge",
    "concept",
}

# Default weights for the five-dimension weighted-sum importance. The
# ImportanceScorer already blends per-preset weights, but the LLM is asked
# to score each dimension independently on [0, 1]; we collapse them with a
# fixed template that mirrors the GENERAL preset.
_IMPORTANCE_WEIGHTS = {
    "recency": 0.25,
    "frequency": 0.15,
    "relevance": 0.25,
    "emotional_weight": 0.15,
    "actionability": 0.20,
}

_INGEST_PROMPT = (
    "你是记忆抽取助手。分析以下记忆原文,抽取实体-关系并评分。严格输出 JSON(无多余文本):\n"
    "{{\n"
    '  "entities": [{{"name": str, "type": "identity|goal|trait|capability|knowledge|concept", '
    '"properties": {{任意键值}}}}, ...],\n'
    '  "relations": [{{"subject": str, "predicate": str, "object": str, "weight": 0.0-1.0}}, ...],\n'
    '  "importance": {{\n'
    '    "recency": 0.0-1.0, "frequency": 0.0-1.0, "relevance": 0.0-1.0, '
    '"emotional_weight": 0.0-1.0, "actionability": 0.0-1.0\n'
    "  }},\n"
    '  "identity_category": "IDENTITY|GOAL|TRAIT|KNOWLEDGE|NONE"\n'
    "}}\n"
    "说明:identity_category 标注该记忆是否承载 agent 身份(我是谁/目标/特质/知识);"
    "无身份信息时填 NONE。entities/relations 可为空数组。\n\n记忆原文:\n{content}"
)


@dataclass
class IngestorResult:
    """Outcome of an :meth:`IngestorAgent.ingest` call."""

    triggered: bool = False
    skipped: bool = False  # P0 red line: origin == FOREGROUND → early return
    entities_added: int = 0
    relations_added: int = 0
    importance: float = 0.0
    identity_category: str = "NONE"
    degraded: bool = False
    error: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


class IngestorAgent:
    """① Ingestor side agent: LLM semantic ingestion into the KG."""

    def __init__(
        self,
        memory_service: MemoryService,
        llm_client: Any | None = None,
        kg: KnowledgeGraph | None = None,
        scorer: ImportanceScorer | None = None,
    ) -> None:
        self._memory = memory_service
        self._llm = llm_client
        self._kg = kg
        self._scorer = scorer or ImportanceScorer()

    async def ingest(
        self,
        memory_id: str,
        content: str,
        agent_id: str,
        session_id: str,
        origin: str | MemoryOrigin,
        timeout: float | None = None,  # None → _state.SIDELLM_TIMEOUT(默认40,env MEMORY_SIDELLM_TIMEOUT 可配)
    ) -> IngestorResult:
        """Extract entities/relations, score importance, tag identity_category.

        P0 red line: when ``origin`` is FOREGROUND the call returns early
        without touching the memory or the KG — side agents only enrich
        agent-sedimented memories, never user/foreground ones.
        """
        # ── P0 red line ───────────────────────────────────────────────
        origin_val = (
            origin.value if isinstance(origin, MemoryOrigin) else str(origin)
        )
        if origin_val == MemoryOrigin.FOREGROUND.value:
            return IngestorResult(triggered=False, skipped=True)

        if self._kg is None:
            # No KG wired → nothing meaningful to do beyond importance scoring.
            logger.debug("IngestorAgent has no KG; skipping memory %s", memory_id)
            return IngestorResult(triggered=False, error="no_kg")

        result = IngestorResult(triggered=True)

        from src.services import _state
        if timeout is None:
            timeout = _state.SIDELLM_TIMEOUT
        try:
            raw = await asyncio.wait_for(
                self._extract(content), timeout=timeout
            )
        except asyncio.TimeoutError:
            logger.warning(
                "IngestorAgent LLM extract timed out (%.1fs) — degrading",
                timeout,
            )
            return await self._degrade(memory_id, content)
        except Exception as exc:
            logger.warning("IngestorAgent extract failed: %s — degrading", exc)
            return await self._degrade(memory_id, content)

        try:
            await self._parse_ingest(memory_id, raw, result)
        except Exception as exc:
            # _parse_ingest is layered-tolerant; reaching here is exceptional.
            logger.warning("IngestorAgent parse failed: %s — degrading", exc)
            return await self._degrade(memory_id, content)
        return result

    # ── LLM extraction ───────────────────────────────────────────────

    async def _extract(self, content: str) -> dict[str, Any]:
        """LLM-extract entities/relations/importance/identity_category.

        Returns a parsed dict; any LLM failure propagates to
        :meth:`ingest` which degrades.
        """
        if self._llm is None:
            # No LLM → degrade immediately (caller handles).
            raise RuntimeError("no_llm_client")
        prompt = _INGEST_PROMPT.format(content=(content or "")[:3000])
        response = await self._llm.chat(
            [{"role": "user", "content": prompt}],
            max_tokens=600,
            temperature=0.2,
        )
        return self._parse_response(response or "")

    # ── Parsing with layered fault tolerance ─────────────────────────

    def _parse_response(self, text: str) -> dict[str, Any]:
        """Parse the JSON blob out of the LLM response.

        Tolerates surrounding prose / fenced code blocks. Returns an empty
        dict on any structural failure (the caller then degrades).
        """
        body = text.strip()
        # Strip ```json ... ``` fences if present.
        if body.startswith("```"):
            body = body.strip("`")
            if body.lower().startswith("json"):
                body = body[4:]
        start = body.find("{")
        end = body.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return {}
        try:
            return json.loads(body[start : end + 1])
        except (json.JSONDecodeError, ValueError):
            return {}

    async def _parse_ingest(
        self,
        memory_id: str,
        raw: dict[str, Any],
        result: IngestorResult,
    ) -> None:
        """Write entities/relations/importance/identity_category back.

        Layered fault tolerance: any single missing field is non-fatal.
        A single entity missing ``name``/``type`` is skipped (not fatal).
        """
        if not raw:
            # Empty parse → behave like a degrade but without re-calling LLM.
            raise RuntimeError("empty_parse")

        # ── entities ──────────────────────────────────────────────────
        for ent in raw.get("entities", []) or []:
            if not isinstance(ent, dict):
                continue
            name = (ent.get("name") or "").strip()
            etype = (ent.get("type") or "").strip().lower()
            if not name or not etype or etype not in _ENTITY_TYPES:
                # Missing name/type → skip just this entity.
                continue
            try:
                self._kg.add_entity(
                    Entity(
                        name=name,
                        entity_type=etype,
                        properties=ent.get("properties") or {},
                        source_memory_ids=[memory_id],
                    )
                )
                result.entities_added += 1
            except Exception as exc:  # noqa: BLE001 - non-fatal
                logger.debug("IngestorAgent add_entity skipped (%s): %s", name, exc)

        # ── relations ─────────────────────────────────────────────────
        for rel in raw.get("relations", []) or []:
            if not isinstance(rel, dict):
                continue
            subj = (rel.get("subject") or "").strip()
            obj = (rel.get("object") or "").strip()
            predicate = (rel.get("predicate") or "").strip()
            if not subj or not obj or not predicate:
                continue
            s_id = self._resolve_entity_id(subj)
            o_id = self._resolve_entity_id(obj)
            if not s_id or not o_id:
                # Can't resolve endpoint entities → skip just this relation.
                continue
            try:
                weight = float(rel.get("weight", 1.0))
            except (TypeError, ValueError):
                weight = 1.0
            weight = max(0.0, min(1.0, weight))
            try:
                self._kg.add_relation(
                    Relation(
                        source_entity_id=s_id,
                        target_entity_id=o_id,
                        relation_type=predicate,
                        weight=weight,
                        source_memory_id=memory_id,
                    )
                )
                result.relations_added += 1
            except Exception as exc:  # noqa: BLE001 - non-fatal
                logger.debug("IngestorAgent add_relation skipped: %s", exc)

        # ── importance (five-dimension weighted sum) ──────────────────
        total = self._weighted_importance(raw.get("importance") or {})
        result.importance = total

        # ── identity_category tag ─────────────────────────────────────
        cat = (raw.get("identity_category") or "NONE").strip().upper()
        if cat not in _IDENTITY_CATEGORIES:
            cat = "NONE"
        result.identity_category = cat

        # ── importance 卫生:失智/无信息回复降分(0f098a9f 类,协作需求)─
        hygiene_content = ""
        try:
            _item = await self._memory.get(memory_id)
            if isinstance(_item, MemoryItem):
                hygiene_content = _item.content or ""
        except Exception:  # noqa: BLE001 - non-fatal
            pass
        total, low_info = self._apply_importance_hygiene(hygiene_content, total)
        result.importance = total

        # ── write back to the memory item ─────────────────────────────
        _meta = {"identity_category": cat, "degraded": False}
        if low_info:
            _meta["low_info_reply"] = True
        try:
            await self._memory.update(
                memory_id=memory_id,
                importance=total,
                metadata=_meta,
            )
        except Exception as exc:  # noqa: BLE001 - non-fatal
            logger.debug("IngestorAgent memory.update skipped: %s", exc)

    def _weighted_importance(self, importance: dict[str, Any]) -> float:
        """Collapse the five-dimension LLM scores into a single [0, 1] total."""
        total = 0.0
        for dim, weight in _IMPORTANCE_WEIGHTS.items():
            try:
                v = float(importance.get(dim, 0.0))
            except (TypeError, ValueError):
                v = 0.0
            total += weight * max(0.0, min(1.0, v))
        return round(total, 4)

    def _apply_importance_hygiene(
        self, content: str, importance: float
    ) -> tuple[float, bool]:
        """importance 卫生:失忆/无信息回复降分,避免排第一污染召回。

        协作需求(0f098a9f 类失智回复被 scorer 算高分排第一)。纯 post-filter,
        不改 scorer 五维评分(红线)。命中 ``_LOW_INFO_PATTERNS`` → cap 0.3 +
        返回 low_info=True(写 ``metadata.low_info_reply`` 供下游降权/过滤)。
        """
        if not content or importance <= 0.0:
            return importance, False
        if _LOW_INFO_PATTERNS.search(content):
            return min(importance, 0.3), True
        return importance, False

    def _resolve_entity_id(self, name: str) -> str:
        """Resolve an entity name (just ingested) to its KG id.

        Returns "" if the entity cannot be found (relation endpoint
        unresolvable → caller skips just that relation).
        """
        if self._kg is None or not name:
            return ""
        try:
            ent = self._kg.find_entity_by_name(name)
        except Exception as exc:  # noqa: BLE001 - non-fatal
            logger.debug("IngestorAgent entity lookup failed (%s): %s", name, exc)
            return ""
        if isinstance(ent, dict):
            return ent.get("id", "") or ""
        return getattr(ent, "id", "") or ""

    # ── Degradation path ─────────────────────────────────────────────

    async def _degrade(
        self,
        memory_id: str,
        content: str,
    ) -> IngestorResult:
        """Fallback: regex KG extraction + deterministic scorer importance.

        Marks the memory ``degraded=True`` so downstream agents know the
        LLM pass was skipped.
        """
        from src.services import _state
        _state.record_degrade("ingestor")
        result = IngestorResult(triggered=True, degraded=True)
        try:
            if self._kg is not None:
                out = self._kg.extract_and_ingest(content or "", memory_id)
                result.entities_added = len(out.get("entity_ids", []))
                result.relations_added = len(out.get("relation_ids", []))
        except Exception as exc:  # noqa: BLE001 - non-fatal
            logger.warning("IngestorAgent degrade KG extraction failed: %s", exc)
            result.error = str(exc)

        # Deterministic importance from the stored item (if available).
        total = 0.0
        item = None
        try:
            item = await self._memory.get(memory_id)
            if isinstance(item, MemoryItem):
                total = self._scorer.score(item).total
        except Exception as exc:  # noqa: BLE001 - non-fatal
            logger.debug("IngestorAgent degrade scorer failed: %s", exc)
        # importance 卫生:失智/无信息回复降分(content 参数优先,回退 item.content)
        _hyg_content = content or (
            item.content if isinstance(item, MemoryItem) else ""
        )
        total, low_info = self._apply_importance_hygiene(_hyg_content, total)
        result.importance = total

        _meta = {"identity_category": "NONE", "degraded": True}
        if low_info:
            _meta["low_info_reply"] = True
        try:
            await self._memory.update(
                memory_id=memory_id,
                importance=total,
                metadata=_meta,
            )
        except Exception as exc:  # noqa: BLE001 - non-fatal
            logger.debug("IngestorAgent degrade memory.update skipped: %s", exc)
        return result


# ── Hook ──────────────────────────────────────────────────────────────


class IngestorHook(MemoryHook):
    """Event-bus hook bridging ``EventType.INGEST`` to :class:`IngestorAgent`.

    Priority is OBSERVER (side-agent enrichment runs after the SYSTEM core
    store/migrate). Only ``on_ingest`` is overridden; register explicitly
    for ``EventType.INGEST`` (see event_bus.py audit note).
    """

    priority = HookPriority.OBSERVER

    def __init__(self, agent: IngestorAgent) -> None:
        self._agent = agent

    async def on_ingest(self, ctx: IngestContext) -> IngestorResult:
        return await self._agent.ingest(
            memory_id=ctx.memory_id,
            content=ctx.content,
            agent_id=ctx.agent_id,
            session_id=ctx.session_id,
            origin=ctx.origin,
        )
