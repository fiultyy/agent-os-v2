# ARCHIVED — side-agent parallel mechanism (pre-AO2),不挂入系统,待用 AO2 capability 重接。
"""SidelineTranscriber — 将 transcript 异步转化为 KG entities/relations.

设计原则（来自 SidelineMemoryAgent）：
- 不依赖 LLM 遵循 prompt（用结构化 I/O）
- 使用 LLM 仅在正则提取失败时做关系提取
- 持续监听 session transcript 变化
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.memory.knowledge_graph import KnowledgeGraph, Relation
from src.memory.knowledge_graph import EntityExtractor

logger = logging.getLogger(__name__)


# ── Data structures ───────────────────────────────────────────────────


@dataclass
class ActionUnit:
    """一个完整的工具调用单元，从 transcript 中归组而来。

    Attributes:
        id: 唯一标识。
        timestamp: 该单元首个事件的 ISO-8601 时间戳。
        user_intent: 从 user message 提取的意图摘要。
        assistant_decision: 从 assistant message 提取的决策摘要。
        tool_calls: 工具调用列表 ``[{name, params}]``。
        tool_results: 工具结果列表（只记录结构，不处理内容）。
        parent_id: 父 ActionUnit ID（时序链）。
    """

    id: str = ""
    timestamp: str = ""
    user_intent: str = ""
    assistant_decision: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    tool_results: list[dict[str, Any]] = field(default_factory=list)
    parent_id: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            self.id = str(uuid.uuid4())


# ── SidelineTranscriber ──────────────────────────────────────────────


class SidelineTranscriber:
    """持续将 transcript 转化为 KG 的 Sideline Agent。

    处理流程:
    1. 解析 JSONL transcript 流
    2. 归组为 ActionUnit（user → assistant → tool_calls → tool_results）
    3. 用 EntityExtractor 提取 entities（正则）
    4. 正则失败时用 LLM 提取 relations（仅此场景调用 LLM）
    5. 批量写入 SQLite KG
    """

    def __init__(
        self,
        knowledge_graph: KnowledgeGraph,
        llm_client: Any | None = None,
    ) -> None:
        self._kg = knowledge_graph
        self._extractor = EntityExtractor()
        self._llm = llm_client  # 可选，仅用于关系提取

    # ── JSONL Parsing ─────────────────────────────────────────────

    def parse_jsonl_to_action_units(self, jsonl_path: str) -> list[ActionUnit]:
        """解析 JSONL 文件，归组为 ActionUnits。

        支持的 JSONL 格式（OpenClaw transcript）:
        每行一个 JSON 对象，包含 ``role`` 和 ``content`` 字段。
        额外支持 ``tool_calls`` 和 ``tool_call_id`` 字段。

        Args:
            jsonl_path: JSONL 文件路径。

        Returns:
            归组后的 ActionUnit 列表。
        """
        path = Path(jsonl_path)
        if not path.exists():
            logger.warning("JSONL file not found: %s", jsonl_path)
            return []

        messages: list[dict[str, Any]] = []
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            try:
                messages.append(json.loads(line))
            except json.JSONDecodeError:
                logger.debug("Skipping malformed JSONL line %d", line_no)
                continue

        return self._group_messages_to_units(messages)

    def _group_messages_to_units(
        self, messages: list[dict[str, Any]],
    ) -> list[ActionUnit]:
        """将消息列表按 user→assistant→tool 归组为 ActionUnit。"""
        units: list[ActionUnit] = []
        current_unit: ActionUnit | None = None
        prev_unit_id: str = ""

        for msg in messages:
            role = msg.get("role", "")

            if role == "user":
                # Start a new ActionUnit
                current_unit = ActionUnit(
                    timestamp=msg.get(
                        "timestamp",
                        datetime.now(timezone.utc).isoformat(),
                    ),
                    user_intent=self._extract_intent(msg.get("content", "")),
                    parent_id=prev_unit_id,
                )
            elif role == "assistant" and current_unit is not None:
                current_unit.assistant_decision = self._extract_decision(
                    msg.get("content", ""),
                )
                # Collect tool calls if present
                raw_tool_calls = msg.get("tool_calls", [])
                for tc in raw_tool_calls:
                    func = tc.get("function", {})
                    current_unit.tool_calls.append({
                        "name": func.get("name", ""),
                        "params": func.get("arguments", {}),
                    })
            elif role == "tool" and current_unit is not None:
                # Record tool result structure (not content)
                current_unit.tool_results.append({
                    "tool_call_id": msg.get("tool_call_id", ""),
                    "name": msg.get("name", ""),
                    "status": "ok" if msg.get("content") else "empty",
                })
                # Finalize the unit after tool result
                unit_id = current_unit.id
                units.append(current_unit)
                prev_unit_id = unit_id
                current_unit = None

        # Handle trailing unit without tool result
        if current_unit is not None:
            units.append(current_unit)

        return units

    @staticmethod
    def _extract_intent(content: str) -> str:
        """从 user message 提取意图摘要（取前 200 字符）。"""
        if isinstance(content, list):
            # Multi-part content: concatenate text parts
            parts = [
                p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"
            ]
            content = " ".join(parts)
        text = str(content).strip()
        return text[:200]

    @staticmethod
    def _extract_decision(content: str) -> str:
        """从 assistant message 提取决策摘要（取前 200 字符）。"""
        if isinstance(content, list):
            parts = [
                p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"
            ]
            content = " ".join(parts)
        text = str(content).strip()
        return text[:200]

    # ── Entity / Relation Extraction + KG Ingest ──────────────────

    def extract_and_ingest_batch(self, action_units: list[ActionUnit]) -> dict:
        """批量提取并写入 KG。

        Returns:
            统计信息 ``{"entities": int, "relations": int, "units": int}``。
        """
        total_entities = 0
        total_relations = 0

        for unit in action_units:
            # 1. Extract entities from tool call names/params
            for tc in unit.tool_calls:
                text = f"{tc.get('name', '')} {json.dumps(tc.get('params', {}), ensure_ascii=False)}"
                entities = self._extractor.extract_entities(text, memory_id=unit.id)
                for entity in entities:
                    self._kg.add_entity(entity)
                    total_entities += 1

            # 2. Extract relations from intent + decision text
            combined = f"{unit.user_intent} {unit.assistant_decision}".strip()
            if combined:
                relations = self._extractor.extract_relations(combined, memory_id=unit.id)

                # 3. Fallback: try LLM extraction if regex found nothing
                if not relations and self._llm is not None:
                    relations = self._llm_extract_relations(combined)

                for relation in relations:
                    self._kg.add_relation(relation)
                    total_relations += 1

        logger.info(
            "Ingested %d entities and %d relations from %d action units",
            total_entities, total_relations, len(action_units),
        )
        return {
            "entities": total_entities,
            "relations": total_relations,
            "units": len(action_units),
        }

    def _classify_action_unit(self, au: dict) -> dict:
        """
        Classify a single ActionUnit as relevant or discarded.

        Returns {"relevant": bool, "reason": str, "fact": dict}
        """
        has_tools = bool(au.get("tool_calls"))
        has_results = bool(au.get("tool_results"))
        intent = au.get("user_intent", "")

        # Trivial/chitchat intents
        trivial_intents = {"hi", "hello", "hey", "how are you", "thanks", "thank you"}
        is_trivial = intent.lower().strip() in trivial_intents if intent else True

        if not has_tools or is_trivial:
            return {
                "relevant": False,
                "reason": "no_tool_calls" if not has_tools else "trivial_intent",
                "fact": au,
            }

        # Has tool calls - check if successful or failed
        outcome = "success"
        if has_results:
            # Simple heuristic: if any result contains error/fail, mark as failure
            for r in au.get("tool_results", []):
                result_str = str(r.get("result", "")).lower()
                if "error" in result_str or "fail" in result_str or "exception" in result_str:
                    outcome = "failure"
                    break

        return {
            "relevant": True,
            "reason": outcome,
            "fact": {
                **au,
                "outcome": outcome,
            },
        }

    def process_action_units(
        self,
        action_units: list[dict],
        scoring_signal: Any = None,
    ) -> dict:
        """
        Process action units and classify into relevant/discarded facts.

        Returns dict matching TRANSCRIBER_SPEC.output_schema:
        {
            "relevant_facts": [...],
            "discarded_facts": [...],
            "extraction_metadata": {
                "total_action_units": int,
                "relevant_count": int,
                "discarded_count": int,
            }
        }
        """
        relevant_facts = []
        discarded_facts = []

        for au in action_units:
            classification = self._classify_action_unit(au)
            if classification["relevant"]:
                relevant_facts.append(classification["fact"])
            else:
                discarded_facts.append(classification["fact"])

        return {
            "relevant_facts": relevant_facts,
            "discarded_facts": discarded_facts,
            "extraction_metadata": {
                "total_action_units": len(action_units),
                "relevant_count": len(relevant_facts),
                "discarded_count": len(discarded_facts),
            },
        }

    def _llm_extract_relations(self, text: str) -> list[Relation]:
        """LLM 辅助关系提取（仅在正则提取失败时调用）。

        当前为预留接口：如果 ``self._llm`` 为 None 或不支持
        ``chat`` 方法，返回空列表。

        当 LLM 可用时，prompt 引导提取 subject-predicate-object，
        返回解析后的 Relation 列表。
        """
        if self._llm is None:
            return []

        try:
            prompt = (
                "Extract factual relations from the following text as JSON array.\n"
                'Each relation: {"subject": "...", "predicate": "...", "object": "..."}\n'
                "Return ONLY the JSON array, no explanation.\n\n"
                f"Text: {text[:1000]}"
            )
            response = self._llm.chat(prompt)  # type: ignore[union-attr]
            if not response:
                return []

            # Parse JSON array from response
            raw = response.strip()
            # Strip markdown code fences if present
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)

            items = json.loads(raw)
            if not isinstance(items, list):
                return []

            relations: list[Relation] = []
            for item in items[:10]:
                if not isinstance(item, dict):
                    continue
                s = item.get("subject", "").strip()
                p = item.get("predicate", "").strip()
                o = item.get("object", "").strip()
                if s and p and o:
                    relations.append(Relation(
                        source_entity_id=s,
                        target_entity_id=o,
                        relation_type=p.replace(" ", "_").lower(),
                    ))
            return relations

        except (json.JSONDecodeError, AttributeError, Exception) as exc:
            logger.debug("LLM relation extraction failed: %s", exc)
            return []
