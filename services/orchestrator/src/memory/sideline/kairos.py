"""KairosAgent — 时机感知 agent.

LIF (Leakage-Integration-Fire) 电位触发时机注入。
当 session 电位达到阈值时，注入时机建议。

参考 MemGPT 的时机感知机制，但做轻量化实现：
- MemGPT: 时机感知通过 LLM 分类器判断
- KairosAgent: 通过 LIF 电位阈值触发（无需 LLM）
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional


@dataclass
class LIFState:
    """LIF 电位状态"""
    potential: float = 0.0
    threshold: float = 0.8
    decay_rate: float = 0.05
    last_update: str = ""
    fire_count: int = 0


class KairosAgent:
    """
    时机感知 agent。

    核心机制：
    1. 每个 session 维护一个 LIF 电位状态
    2. 每次 update_potential() 根据重要性提升电位
    3. 决策点额外 +0.2 电位加成
    4. 每轮自动衰减 5%
    5. 电位达到阈值时触发 injection

    典型用法：
        agent = KairosAgent(memory_service, threshold=0.8)
        agent.update_potential("session-1", importance=0.6, is_decision_point=False)
        if agent.should_inject("session-1"):
            injection = await agent.get_injection("session-1", context)
    """

    def __init__(self, memory_service, threshold: float = 0.8, decay_rate: float = 0.05, max_sessions: int = 1000):
        """
        Args:
            memory_service: 记忆服务（用于获取上下文）
            threshold: 触发阈值，默认 0.8
            decay_rate: 每轮衰减率，默认 0.05
            max_sessions: 最大 session 数，超出时淘汰最旧的，默认 1000
        """
        self._memory = memory_service
        self._threshold = threshold
        self._decay_rate = decay_rate
        self._max_sessions = max_sessions
        self._lif: dict[str, LIFState] = {}

    def update_potential(self, session_id: str, importance: float, is_decision_point: bool = False) -> float:
        """
        更新 LIF 电位。

        电位变化规则：
        - 基础增加: importance * 1.0
        - 决策点加成: importance * 1.2
        - 每轮结束时: 电位衰减 decay_rate

        Args:
            session_id: 会话 ID
            importance: 重要性评分 (0-1)
            is_decision_point: 是否为决策点（额外 +0.2 加成）

        Returns:
            当前电位值
        """
        # Evict oldest session if at capacity
        if session_id not in self._lif and len(self._lif) >= self._max_sessions:
            oldest = min(self._lif.items(), key=lambda x: x[1].last_update)
            del self._lif[oldest[0]]

        state = self._lif.setdefault(session_id, LIFState(
            threshold=self._threshold,
            decay_rate=self._decay_rate
        ))

        # 计算增量
        multiplier = 1.2 if is_decision_point else 1.0
        increment = importance * multiplier

        # 更新电位（衰减后再增加，模拟"积累-触发"模式）
        state.potential = state.potential * (1 - self._decay_rate) + increment
        state.potential = min(1.0, state.potential)
        state.last_update = datetime.now(timezone.utc).isoformat()

        return state.potential

    def should_inject(self, session_id: str) -> bool:
        """判断是否应该注入时机建议"""
        state = self._lif.get(session_id)
        if state is None:
            return False
        return state.potential >= state.threshold

    def get_potential(self, session_id: str) -> float:
        """获取当前电位值（用于调试）"""
        state = self._lif.get(session_id)
        return state.potential if state else 0.0

    async def get_injection(self, session_id: str, context: dict) -> Optional[str]:
        """
        生成时机注入内容。

        当 should_inject() 返回 True 时调用此方法获取注入内容。
        注入内容包含当前电位状态和建议。

        Args:
            session_id: 会话 ID
            context: 上下文 dict，包含 session_summary, recent_actions 等

        Returns:
            注入文本，或 None（电位未达到阈值）
        """
        if not self.should_inject(session_id):
            return None

        state = self._lif[session_id]

        # 构建注入内容
        session_summary = context.get("session_summary", "")
        recent_memories = context.get("recent_memories", [])
        recent_summary = self._summarize_recent(recent_memories)

        injection_parts = [
            f"[时机建议] 电位触发 (potential={state.potential:.2f}, threshold={state.threshold})",
            f"Session: {session_id}",
            f"最后更新: {state.last_update}",
            f"触发次数: {state.fire_count}",
        ]

        if session_summary:
            injection_parts.append(f"上下文摘要: {session_summary[:100]}")

        if recent_summary:
            injection_parts.append(f"最近记忆: {recent_summary}")

        # 重置电位（但保留部分残余，避免完全归零）
        state.potential = state.potential * 0.1  # 重置为 10%
        state.fire_count += 1

        return "\n".join(injection_parts)

    def _summarize_recent(self, recent_memories: list[dict], max_chars: int = 80) -> str:
        """总结最近记忆（用于注入）"""
        if not recent_memories:
            return ""

        parts = []
        for mem in recent_memories[:3]:
            content = mem.get("content", "") or mem.get("text", "")
            if content:
                parts.append(content[:30])

        result = "; ".join(parts)
        if len(result) > max_chars:
            result = result[:max_chars] + "..."
        return result

    def reset_session(self, session_id: str) -> None:
        """重置 session 的电位状态"""
        if session_id in self._lif:
            del self._lif[session_id]

    def get_stats(self) -> dict:
        """获取全局统计（用于监控）"""
        total_sessions = len(self._lif)
        firing_sessions = sum(1 for s in self._lif.values() if s.potential >= s.threshold)
        return {
            "total_sessions": total_sessions,
            "firing_sessions": firing_sessions,
            "avg_potential": sum(s.potential for s in self._lif.values()) / total_sessions if total_sessions else 0.0,
        }