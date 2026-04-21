"""ConditionalSpawner — 条件型任务生成器。

支持三种触发模式：
- cron: 定时触发（通过 croniter 解析 cron 表达式）
- event: 事件触发（通过 event_bus 发布/订阅）
- queue: 队列满时触发（维护一个内部计数器）

实现参考：
- OpenClaw 的 cron spawning（见 tools/openclaw/src/infra/heartbeat-runner.ts）
- OpenClaw 的 spawn 机制（见 tools/openclaw/src/acp/control-plane/spawn.ts）
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class TriggerType(Enum):
    """触发器类型枚举。"""

    CRON = "cron"
    EVENT = "event"
    QUEUE = "queue"


@dataclass
class SpawnConfig:
    """Spawn 配置，包含触发条件和 agent 创建参数。"""

    trigger: TriggerType
    condition: str | None  # cron 表达式 / event pattern / queue threshold
    agent_type: str
    config: dict[str, Any]  # agent 配置
    max_concurrent: int = 1


class ConditionalSpawner:
    """条件型任务生成器。

    管理多种触发类型的 spawn 配置，根据触发条件动态创建 subagent。

    触发模式说明：
    - CRON: 使用 croniter 解析 cron 表达式，定时触发
    - EVENT: 订阅 event_bus 的事件，事件匹配时触发
    - QUEUE: 维护一个计数器，累积到阈值时触发
    """

    def __init__(self, agent_manager, event_bus=None) -> None:
        """初始化 ConditionalSpawner。

        Args:
            agent_manager: Agent 管理器，用于创建 subagent。
            event_bus: 可选的事件总线，用于 EVENT 触发模式。
        """
        self._agent_manager = agent_manager
        self._event_bus = event_bus
        self._configs: list[SpawnConfig] = []
        self._active_agents: dict[str, dict] = {}
        self._queue_counts: dict[str, int] = {}
        self._cron_schedules: dict[str, Any] = {}  # croniter instances
        self._last_cron_check: dict[str, datetime] = {}

        # 延迟导入 croniter，避免在未安装时无法 import
        self._croniter = None

    def _get_croniter(self):
        """懒加载 croniter。"""
        if self._croniter is None:
            try:
                import croniter
                self._croniter = croniter
            except ImportError:
                raise ImportError(
                    "croniter is required for CRON trigger support. "
                    "Install with: pip install croniter"
                )
        return self._croniter

    def register(self, config: SpawnConfig) -> None:
        """注册一个 spawn 配置。

        Args:
            config: SpawnConfig 实例。
        """
        self._configs.append(config)

        # 初始化队列计数器
        if config.trigger == TriggerType.QUEUE:
            self._queue_counts[config.agent_type] = 0

        # 注意：CRON 的 croniter 延迟到 _should_spawn_cron 时才初始化（懒加载）

    def _should_spawn_cron(self, config: SpawnConfig) -> bool:
        """检查 CRON 触发器是否应该触发。"""
        if config.trigger != TriggerType.CRON:
            return False
        if not config.condition:
            return False

        croniter = self._get_croniter()

        # 懒初始化 croniter schedule（统一使用本地时间避免时区混乱）
        if config.agent_type not in self._cron_schedules:
            # croniter 接受 naive datetime，使用系统本地时间
            self._cron_schedules[config.agent_type] = croniter.croniter(
                config.condition, datetime.now()
            )
            self._last_cron_check[config.agent_type] = datetime.now()

        sched = self._cron_schedules.get(config.agent_type)
        if sched is None:
            return False

        now = datetime.now()
        # 尝试获取下一个触发时间
        try:
            next_time = sched.get_next(datetime)
            if next_time is None:
                return False
            # 如果下一个触发时间已过，说明应该触发
            if next_time <= now:
                return True
            # 未到触发时间，重置 schedule 到当前时间避免内部指针漂移
            self._cron_schedules[config.agent_type] = croniter.croniter(
                config.condition, now
            )
            self._last_cron_check[config.agent_type] = now
            return False
        except (ValueError, IndexError):
            return False

    def _should_spawn_event(self, config: SpawnConfig, event: str) -> bool:
        """检查 EVENT 触发器是否应该触发。"""
        if config.trigger != TriggerType.EVENT:
            return False
        if not config.condition:
            return False

        # 支持通配符模式匹配: event:* 或 event:task:*
        pattern = config.condition
        if pattern == "*":
            return True
        if pattern.endswith("*"):
            prefix = pattern[:-1]
            return event.startswith(prefix)
        return event == pattern

    def _should_spawn_queue(self, config: SpawnConfig) -> bool:
        """检查 QUEUE 触发器是否应该触发。"""
        if config.trigger != TriggerType.QUEUE:
            return False
        if not config.condition:
            return False

        try:
            threshold = int(config.condition)
        except (ValueError, TypeError):
            return False

        count = self._queue_counts.get(config.agent_type, 0)
        return count >= threshold

    async def spawn(self, trigger: TriggerType, context: dict) -> str | None:
        """触发 spawn，返回 agent_id 或 None。

        Args:
            trigger: 触发类型。
            context: 触发上下文，包含 event（事件触发）、queue_delta（队列触发）等。

        Returns:
            新创建的 agent_id，或 None（未匹配到可触发的配置）。
        """
        event = context.get("event", "")
        queue_delta = context.get("queue_delta", 0)

        for config in self._configs:
            # 检查并发限制
            active_count = sum(
                1 for a in self._active_agents.values()
                if a.get("agent_type") == config.agent_type
            )
            if active_count >= config.max_concurrent:
                continue

            should_spawn = False

            if trigger == TriggerType.CRON:
                should_spawn = self._should_spawn_cron(config)
            elif trigger == TriggerType.EVENT:
                should_spawn = self._should_spawn_event(config, event)
            elif trigger == TriggerType.QUEUE:
                # 更新队列计数
                if config.agent_type in self._queue_counts:
                    self._queue_counts[config.agent_type] += queue_delta
                should_spawn = self._should_spawn_queue(config)

            if should_spawn:
                # 调用 agent_manager 创建 subagent
                agent_id = await self._create_subagent(config)
                if agent_id:
                    logger.info(
                        f"Spawned subagent {agent_id} via {trigger.value} trigger"
                    )
                    return agent_id

        return None

    async def _create_subagent(self, config: SpawnConfig) -> str | None:
        """创建 subagent。"""
        if hasattr(self._agent_manager, "create_subagent"):
            result = await self._agent_manager.create_subagent(
                agent_type=config.agent_type,
                config=config.config,
            )
            if isinstance(result, dict):
                agent_id = result.get("id") or result.get("agent_id")
            elif isinstance(result, str):
                agent_id = result
            else:
                agent_id = None

            if agent_id:
                self._active_agents[agent_id] = {
                    "agent_id": agent_id,
                    "agent_type": config.agent_type,
                    "config": config.config,
                    "spawned_at": datetime.now(timezone.utc).isoformat(),
                    "status": "running",
                }
                return agent_id

        # Fallback: 直接调用 agent_manager
        try:
            agent_id = await self._agent_manager(
                agent_type=config.agent_type,
                **config.config,
            )
            if agent_id:
                self._active_agents[agent_id] = {
                    "agent_id": agent_id,
                    "agent_type": config.agent_type,
                    "config": config.config,
                    "spawned_at": datetime.now(timezone.utc).isoformat(),
                    "status": "running",
                }
                return agent_id
        except Exception as e:
            logger.error(f"Failed to create subagent: {e}")

        return None

    def get_active_agents(self) -> list[dict]:
        """返回当前活跃的 subagent 列表。

        Returns:
            活跃 agent 信息列表。
        """
        return [
            {
                "agent_id": aid,
                "agent_type": info["agent_type"],
                "status": info["status"],
                "spawned_at": info["spawned_at"],
            }
            for aid, info in self._active_agents.items()
            if info["status"] == "running"
        ]

    def mark_agent_done(self, agent_id: str) -> None:
        """标记 agent 已完成。"""
        if agent_id in self._active_agents:
            self._active_agents[agent_id]["status"] = "completed"

    def mark_agent_failed(self, agent_id: str, error: str = "") -> None:
        """标记 agent 失败。"""
        if agent_id in self._active_agents:
            self._active_agents[agent_id]["status"] = "failed"
            self._active_agents[agent_id]["error"] = error

    def enqueue(self, agent_type: str, delta: int = 1) -> None:
        """入队，增加队列计数（用于 QUEUE 触发）。"""
        if agent_type not in self._queue_counts:
            self._queue_counts[agent_type] = 0
        self._queue_counts[agent_type] += delta

    def dequeue(self, agent_type: str, delta: int = 1) -> None:
        """出队，减少队列计数。"""
        if agent_type in self._queue_counts:
            self._queue_counts[agent_type] = max(0, self._queue_counts[agent_type] - delta)

    def reset_queue(self, agent_type: str) -> None:
        """重置队列计数。"""
        if agent_type in self._queue_counts:
            self._queue_counts[agent_type] = 0
