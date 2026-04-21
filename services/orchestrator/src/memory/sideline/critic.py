"""CriticAgent — 事实纠正 agent.

当 recall 结果置信度低于阈值时注入纠正。

核心机制：
1. evaluate() 评估 recall_results，标记低置信度项
2. get_corrections() 获取需要纠正的记忆项
3. 纠正内容注入到当前上下文

参考 MemGPT 的 verifier 机制，但轻量化实现：
- MemGPT: 使用 LLM 判断记忆的准确性和相关性
- CriticAgent: 使用结构化置信度评分（无需 LLM）
"""

import math
from dataclasses import dataclass
from typing import Optional


@dataclass
class EvaluationResult:
    """单条 recall 结果的评估"""
    memory_id: str
    confidence: float
    needs_correction: bool
    correction_hint: Optional[str] = None
    reason: str = ""


class CriticAgent:
    """
    事实纠正 agent。

    监控 recall 结果的置信度，对低置信度记忆进行纠正。

    用法：
        agent = CriticAgent(memory_service, confidence_threshold=0.5)

        # 评估 recall 结果
        evaluated = await agent.evaluate(recall_results)

        # 获取需要纠正的记忆项
        corrections = await agent.get_corrections(session_id)
    """

    def __init__(self, memory_service, confidence_threshold: float = 0.5):
        """
        Args:
            memory_service: 记忆服务（用于查询和更新记忆）
            confidence_threshold: 置信度阈值，默认 0.5
        """
        self._memory = memory_service
        self._threshold = confidence_threshold
        self._correction_history: list[dict] = []

    async def evaluate(self, recall_results: list[dict]) -> list[EvaluationResult]:
        """
        评估 recall 结果的置信度。

        对每条 recall 结果进行置信度评分：
        - confidence >= threshold: 无需纠正
        - confidence < threshold: 标记为需要纠正

        置信度计算因素：
        - 来源可靠性（来源越权威，置信度越高）
        - 时间衰减（越久远的记忆，置信度越低）
        - 一致性（多条相似记忆相互印证，提高置信度）

        Args:
            recall_results: recall 结果列表，每项包含 memory_id, content 等

        Returns:
            list[EvaluationResult]: 评估结果列表
        """
        results = []

        for item in recall_results:
            memory_id = item.get("memory_id", "")
            content = item.get("content", "") or item.get("text", "")

            # 计算置信度
            confidence = self._compute_confidence(item)

            # 判断是否需要纠正
            needs_correction = confidence < self._threshold

            # 生成纠正提示（如果需要）
            correction_hint = None
            if needs_correction:
                correction_hint = self._generate_correction_hint(item, confidence)

            results.append(EvaluationResult(
                memory_id=memory_id,
                confidence=confidence,
                needs_correction=needs_correction,
                correction_hint=correction_hint,
                reason=f"confidence={confidence:.2f} {'<=' if needs_correction else '>'} threshold={self._threshold}"
            ))

        return results

    async def get_corrections(self, session_id: str) -> list[dict]:
        """
        获取指定 session 需要纠正的记忆项。

        通过 memory_service 查询该 session 的低置信度记忆，
        并生成纠正内容。

        Args:
            session_id: 会话 ID

        Returns:
            list[dict]: 纠正项列表，每项包含 memory_id, original, correction
        """
        if self._memory is None:
            return []

        # 获取该 session 的所有记忆
        if hasattr(self._memory, "get_session_memories"):
            memories = await self._memory.get_session_memories(session_id)
        else:
            return []

        corrections = []

        for mem in memories:
            memory_id = mem.get("memory_id") or mem.get("id", "")
            confidence = mem.get("confidence", 1.0)

            if confidence < self._threshold:
                original = mem.get("content", "") or mem.get("text", "")
                correction = self._generate_correction(mem, confidence)

                corrections.append({
                    "memory_id": memory_id,
                    "original": original,
                    "correction": correction,
                    "confidence": confidence,
                    "session_id": session_id,
                })

        return corrections

    async def get_injection(self, session_id: str, evaluated_results: list[EvaluationResult]) -> str:
        """
        获取需要注入的纠正内容。

        当 evaluated_results 中存在低置信度项时，
        生成纠正注入文本。

        Args:
            session_id: 会话 ID
            evaluated_results: evaluate() 的结果

        Returns:
            str: 注入文本，或空字符串
        """
        low_confidence = [r for r in evaluated_results if r.needs_correction]

        if not low_confidence:
            return ""

        parts = [
            "[事实纠正建议]",
            f"Session: {session_id}",
            f"低置信度项数: {len(low_confidence)}",
        ]

        for result in low_confidence[:5]:  # 最多显示 5 条
            parts.append(f"- 记忆 {result.memory_id[:16]}...: {result.correction_hint or result.reason}")

        return "\n".join(parts)

    def _compute_confidence(self, item: dict) -> float:
        """
        计算单条记忆的置信度。

        评分因素：
        - source_reliability: 来源可靠性 (0-1)
        - age_decay: 时间衰减因子
        - consistency: 一致性评分

        Returns:
            float: 置信度 (0-1)
        """
        # 来源可靠性
        source = item.get("source", "unknown")
        source_weights = {
            "user_input": 1.0,
            "tool_result": 0.9,
            "reflection": 0.8,
            "external": 0.6,
            "unknown": 0.5,
        }
        source_score = source_weights.get(source, 0.5)

        # 时间衰减（假设每 24h 衰减 5%）
        timestamp = item.get("timestamp", "")
        if timestamp:
            try:
                from datetime import datetime, timezone
                dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                age_hours = (datetime.now(timezone.utc) - dt).total_seconds() / 3600
                age_decay = 0.5 + 0.5 * math.exp(-age_hours / (24 * 30))  # 0.5 baseline, exponential decay to 0.5
            except Exception:
                age_decay = 0.8
        else:
            age_decay = 0.8

        # 一致性（与 recent_memories 中相似记忆的匹配度）
        consistency = item.get("consistency", 1.0)

        # 综合评分
        confidence = source_score * 0.4 + age_decay * 0.3 + consistency * 0.3
        return min(1.0, max(0.0, confidence))

    def _generate_correction_hint(self, item: dict, confidence: float) -> str:
        """生成纠正提示"""
        return f"置信度偏低 ({confidence:.2f})，建议核实"

    def _generate_correction(self, mem: dict, confidence: float) -> str:
        """生成纠正内容"""
        original = mem.get("content", "") or mem.get("text", "")
        # 简单实现：标记为需要核实
        return f"[待核实] {original[:50]}..." if len(original) > 50 else f"[待核实] {original}"

    def get_stats(self) -> dict:
        """获取统计信息"""
        return {
            "confidence_threshold": self._threshold,
            "total_corrections": len(self._correction_history),
        }