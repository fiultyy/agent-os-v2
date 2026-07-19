# ARCHIVED — side-agent parallel mechanism (pre-AO2),不挂入系统,待用 AO2 capability 重接
"""DreamerAgent — 记忆巩固 agent.

定时将 session 记忆提炼为 episodic memories，触发 L2→L3 迁移。

参考 MemGPT 的 dreamer 机制：
- MemGPT: 使用 LLM 将对话历史压缩为 summary
- DreamerAgent: 轻量化实现，使用 reflection trigger 触发记忆压缩

核心流程：
1. run() 被定时调用（默认 3600s 间隔）
2. 获取所有活跃 session
3. 对每个 session 调用 consolidate_session()
4. 返回创建的 episodic memories 数量
"""

from datetime import datetime, timezone
from typing import Optional


class DreamerAgent:
    """
    记忆巩固 agent。

    定时将 session 记忆从工作记忆（L2）巩固到情景记忆（L3）。

    用法：
        dreamer = DreamerAgent(memory_service, interval_seconds=3600)
        result = await dreamer.run()
        # result = {"sessions_processed": 5, "episodic_created": 12}
    """

    def __init__(self, memory_service, interval_seconds: int = 3600):
        """
        Args:
            memory_service: 记忆服务（需实现 reflect() 方法）
            interval_seconds: 运行间隔，默认 3600（1小时）
        """
        self._memory = memory_service
        self._interval = interval_seconds
        self._last_run: Optional[str] = None
        self._total_runs: int = 0
        self._registered_sessions: set[str] = set()

    async def run(self) -> dict:
        """
        执行一轮巩固。

        获取所有活跃 session，对每个 session 调用 consolidate_session()。
        最后更新 _last_run 时间戳。

        Returns:
            dict: {
                "sessions_processed": int,  # 处理的成功 session 数
                "episodic_created": int,     # 创建的 episodic memories 数
                "errors": int,              # 遇到的错误数
                "duration_ms": float        # 执行耗时
            }
        """
        start_time = datetime.now(timezone.utc)
        sessions_processed = 0
        episodic_created = 0
        errors = 0

        try:
            # 1. 获取所有活跃 session
            active_sessions = await self._get_active_sessions()

            # 2. 对每个 session 执行巩固
            for session_id in active_sessions:
                try:
                    count = await self.consolidate_session(session_id)
                    sessions_processed += 1
                    episodic_created += count
                except Exception:
                    errors += 1

        finally:
            self._last_run = datetime.now(timezone.utc).isoformat()
            self._total_runs += 1

        duration_ms = (datetime.now(timezone.utc) - start_time).total_seconds() * 1000

        return {
            "sessions_processed": sessions_processed,
            "episodic_created": episodic_created,
            "errors": errors,
            "duration_ms": duration_ms,
            "last_run": self._last_run,
            "total_runs": self._total_runs,
        }

    async def consolidate_session(self, session_id: str) -> int:
        """
        巩固单个 session。

        调用 memory_service.reflect() 触发记忆压缩，
        将当前 session 的工作记忆凝结为 episodic memory。

        Args:
            session_id: 会话 ID

        Returns:
            int: 创建的 episodic memories 数量
        """
        if self._memory is None:
            return 0

        # 调用 reflect，trigger="dreamer" 表示由 DreamerAgent 触发
        result = await self._memory.reflect(
            agent_id="dreamer",
            trigger="dreamer",
            session_id=session_id
        )

        if result is None:
            return 0

        # 解析 reflect 结果
        episodic_count = result.get("episodic_created", 0) if isinstance(result, dict) else 0
        return episodic_count

    async def _get_active_sessions(self) -> list[str]:
        """
        获取所有活跃 session。

        通过 memory_service 列举当前活跃的 session。
        如果 memory_service 没有 list_sessions 方法，返回空列表。

        Returns:
            list[str]: session_id 列表
        """
        if self._memory is None:
            return []

        # 尝试调用 list_sessions
        if hasattr(self._memory, "list_active_sessions"):
            sessions = await self._memory.list_active_sessions()
            return sessions if sessions else []
        elif hasattr(self._memory, "list_sessions"):
            sessions = await self._memory.list_sessions()
            return sessions if sessions else []

        # Fallback to manually registered sessions
        if self._registered_sessions:
            return list(self._registered_sessions)

        # 默认返回空（不支持自动发现）
        return []

    def register_session(self, session_id: str) -> None:
        """Register a session for consolidation."""
        self._registered_sessions.add(session_id)

    def unregister_session(self, session_id: str) -> None:
        """Unregister a session."""
        self._registered_sessions.discard(session_id)

    def get_stats(self) -> dict:
        """获取运行统计"""
        return {
            "interval_seconds": self._interval,
            "total_runs": self._total_runs,
            "last_run": self._last_run,
        }

    async def run_once_for(self, session_id: str) -> dict:
        """
        对指定 session 执行一次巩固（一次性任务）。

        不同于 run() 处理所有活跃 session，
        此方法只处理指定的单个 session。

        Args:
            session_id: 要巩固的 session ID

        Returns:
            dict: 包含 episodic_created 数量
        """
        episodic_count = await self.consolidate_session(session_id)
        return {
            "session_id": session_id,
            "episodic_created": episodic_count,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }