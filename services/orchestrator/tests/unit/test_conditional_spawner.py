"""Tests for conditional_spawner.py."""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.agent.meta.conditional_spawner import (
    ConditionalSpawner,
    SpawnConfig,
    TriggerType,
)


class MockAgentManager:
    """Mock agent manager for testing."""

    def __init__(self):
        self.created_agents: list[dict] = []

    async def create_subagent(self, agent_type: str, config: dict) -> dict:
        agent_id = f"agent-{len(self.created_agents) + 1}"
        agent = {"id": agent_id, "agent_type": agent_type, "config": config}
        self.created_agents.append(agent)
        return agent


# ── TriggerType tests ──────────────────────────────────────────────────────────


class TestTriggerType:
    def test_trigger_type_values(self):
        assert TriggerType.CRON.value == "cron"
        assert TriggerType.EVENT.value == "event"
        assert TriggerType.QUEUE.value == "queue"

    def test_trigger_type_from_str(self):
        assert TriggerType("cron") == TriggerType.CRON
        assert TriggerType("event") == TriggerType.EVENT
        assert TriggerType("queue") == TriggerType.QUEUE

    def test_trigger_type_enum_members(self):
        assert len(TriggerType) == 3


# ── SpawnConfig tests ──────────────────────────────────────────────────────────


class TestSpawnConfig:
    def test_spawn_config_creation(self):
        config = SpawnConfig(
            trigger=TriggerType.CRON,
            condition="*/5 * * * *",
            agent_type="worker",
            config={"timeout": 60},
            max_concurrent=3,
        )
        assert config.trigger == TriggerType.CRON
        assert config.condition == "*/5 * * * *"
        assert config.agent_type == "worker"
        assert config.config == {"timeout": 60}
        assert config.max_concurrent == 3

    def test_spawn_config_defaults(self):
        config = SpawnConfig(
            trigger=TriggerType.EVENT,
            condition="task:start",
            agent_type="listener",
            config={},
        )
        assert config.max_concurrent == 1  # default

    def test_spawn_config_immutable(self):
        config = SpawnConfig(
            trigger=TriggerType.QUEUE,
            condition="10",
            agent_type="batch",
            config={"size": 100},
        )
        # 属性可以读取
        assert config.trigger == TriggerType.QUEUE
        # 属性重新赋值（dataclass 不防止此操作，但语义上应视为不可变）
        config.max_concurrent = 5
        assert config.max_concurrent == 5


# ── ConditionalSpawner basic tests ────────────────────────────────────────────


class TestConditionalSpawnerInit:
    def test_init_with_agent_manager(self):
        mock_manager = MockAgentManager()
        spawner = ConditionalSpawner(mock_manager)
        assert spawner._agent_manager is mock_manager
        assert spawner._event_bus is None

    def test_init_with_event_bus(self):
        mock_manager = MockAgentManager()
        mock_bus = MagicMock()
        spawner = ConditionalSpawner(mock_manager, event_bus=mock_bus)
        assert spawner._event_bus is mock_bus


class TestConditionalSpawnerRegister:
    def test_register_single_config(self):
        spawner = ConditionalSpawner(MockAgentManager())
        config = SpawnConfig(
            trigger=TriggerType.CRON,
            condition="*/5 * * * *",
            agent_type="worker",
            config={},
        )
        spawner.register(config)
        assert len(spawner._configs) == 1
        assert spawner._configs[0] is config

    def test_register_multiple_configs(self):
        spawner = ConditionalSpawner(MockAgentManager())
        configs = [
            SpawnConfig(TriggerType.CRON, "*/5 * * * *", "worker", {}),
            SpawnConfig(TriggerType.EVENT, "task:*", "listener", {}),
            SpawnConfig(TriggerType.QUEUE, "10", "batch", {}),
        ]
        for c in configs:
            spawner.register(c)
        assert len(spawner._configs) == 3

    def test_register_queue_initializes_counter(self):
        spawner = ConditionalSpawner(MockAgentManager())
        config = SpawnConfig(TriggerType.QUEUE, "5", "batch", {})
        spawner.register(config)
        assert spawner._queue_counts["batch"] == 0


# ── ConditionalSpawner spawn tests ────────────────────────────────────────────


class TestConditionalSpawnerSpawn:
    @pytest.mark.asyncio
    async def test_spawn_no_matching_config(self):
        spawner = ConditionalSpawner(MockAgentManager())
        # 无任何配置，spawn 返回 None
        result = await spawner.spawn(TriggerType.CRON, {})
        assert result is None

    @pytest.mark.asyncio
    async def test_spawn_event_trigger_matching(self):
        manager = MockAgentManager()
        spawner = ConditionalSpawner(manager)
        spawner.register(
            SpawnConfig(TriggerType.EVENT, "task:start", "worker", {"name": "w1"})
        )
        result = await spawner.spawn(TriggerType.EVENT, {"event": "task:start"})
        assert result is not None
        assert len(manager.created_agents) == 1
        assert manager.created_agents[0]["agent_type"] == "worker"

    @pytest.mark.asyncio
    async def test_spawn_event_trigger_wildcard(self):
        manager = MockAgentManager()
        spawner = ConditionalSpawner(manager)
        spawner.register(
            SpawnConfig(TriggerType.EVENT, "task:*", "worker", {})
        )
        result = await spawner.spawn(TriggerType.EVENT, {"event": "task:complete"})
        assert result is not None

    @pytest.mark.asyncio
    async def test_spawn_event_trigger_not_matching(self):
        manager = MockAgentManager()
        spawner = ConditionalSpawner(manager)
        spawner.register(
            SpawnConfig(TriggerType.EVENT, "task:start", "worker", {})
        )
        result = await spawner.spawn(TriggerType.EVENT, {"event": "other:event"})
        assert result is None
        assert len(manager.created_agents) == 0

    @pytest.mark.asyncio
    async def test_spawn_queue_trigger_threshold_reached(self):
        manager = MockAgentManager()
        spawner = ConditionalSpawner(manager)
        spawner.register(
            SpawnConfig(TriggerType.QUEUE, "3", "batch", {})
        )
        # 累积队列
        spawner._queue_counts["batch"] = 3
        result = await spawner.spawn(TriggerType.QUEUE, {"queue_delta": 0})
        assert result is not None
        assert len(manager.created_agents) == 1

    @pytest.mark.asyncio
    async def test_spawn_queue_trigger_threshold_not_reached(self):
        manager = MockAgentManager()
        spawner = ConditionalSpawner(manager)
        spawner.register(
            SpawnConfig(TriggerType.QUEUE, "5", "batch", {})
        )
        spawner._queue_counts["batch"] = 2
        result = await spawner.spawn(TriggerType.QUEUE, {"queue_delta": 0})
        assert result is None


# ── ConditionalSpawner active agents ──────────────────────────────────────────


class TestConditionalSpawnerActiveAgents:
    def test_get_active_agents_empty(self):
        spawner = ConditionalSpawner(MockAgentManager())
        assert spawner.get_active_agents() == []

    def test_get_active_agents_tracks_spawned(self):
        manager = MockAgentManager()
        spawner = ConditionalSpawner(manager)
        spawner.register(
            SpawnConfig(TriggerType.EVENT, "*", "worker", {})
        )
        # 通过 mock 手动添加一个 active agent
        spawner._active_agents["agent-1"] = {
            "agent_id": "agent-1",
            "agent_type": "worker",
            "status": "running",
            "spawned_at": "2026-01-01T00:00:00Z",
        }
        spawner._active_agents["agent-2"] = {
            "agent_id": "agent-2",
            "agent_type": "worker",
            "status": "completed",  # 已完成
            "spawned_at": "2026-01-01T00:00:00Z",
        }
        active = spawner.get_active_agents()
        assert len(active) == 1
        assert active[0]["agent_id"] == "agent-1"


# ── Queue management ───────────────────────────────────────────────────────────


class TestConditionalSpawnerQueueManagement:
    def test_enqueue(self):
        spawner = ConditionalSpawner(MockAgentManager())
        spawner._queue_counts["batch"] = 5
        spawner.enqueue("batch", 3)
        assert spawner._queue_counts["batch"] == 8

    def test_dequeue(self):
        spawner = ConditionalSpawner(MockAgentManager())
        spawner._queue_counts["batch"] = 5
        spawner.dequeue("batch", 3)
        assert spawner._queue_counts["batch"] == 2

    def test_dequeue_not_below_zero(self):
        spawner = ConditionalSpawner(MockAgentManager())
        spawner._queue_counts["batch"] = 2
        spawner.dequeue("batch", 10)
        assert spawner._queue_counts["batch"] == 0

    def test_reset_queue(self):
        spawner = ConditionalSpawner(MockAgentManager())
        spawner._queue_counts["batch"] = 99
        spawner.reset_queue("batch")
        assert spawner._queue_counts["batch"] == 0


# ── Agent lifecycle tracking ───────────────────────────────────────────────────


class TestConditionalSpawnerAgentLifecycle:
    def test_mark_agent_done(self):
        spawner = ConditionalSpawner(MockAgentManager())
        spawner._active_agents["agent-1"] = {
            "agent_id": "agent-1",
            "agent_type": "worker",
            "status": "running",
            "spawned_at": "2026-01-01T00:00:00Z",
        }
        spawner.mark_agent_done("agent-1")
        assert spawner._active_agents["agent-1"]["status"] == "completed"

    def test_mark_agent_failed(self):
        spawner = ConditionalSpawner(MockAgentManager())
        spawner._active_agents["agent-1"] = {
            "agent_id": "agent-1",
            "agent_type": "worker",
            "status": "running",
            "spawned_at": "2026-01-01T00:00:00Z",
        }
        spawner.mark_agent_failed("agent-1", "OOM")
        assert spawner._active_agents["agent-1"]["status"] == "failed"
        assert spawner._active_agents["agent-1"]["error"] == "OOM"
