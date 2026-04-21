"""Tests for meta_agent_node.py."""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.agent.meta.meta_agent_node import MetaAgentNode
from src.agent.meta.conditional_spawner import ConditionalSpawner, SpawnConfig, TriggerType
from src.graph.state import GraphState


class TestMetaAgentNodeInit:
    def test_init_with_name_only(self):
        node = MetaAgentNode(name="test_node")
        assert node.name == "test_node"
        assert node.agent_id == ""
        assert node.spawn_config is None
        assert node.spawner is None
        assert node.status == "pending"
        assert node.result is None

    def test_init_with_agent_id(self):
        node = MetaAgentNode(name="test_node", agent_id="agent-123")
        assert node.agent_id == "agent-123"

    def test_init_with_spawn_config(self):
        config = SpawnConfig(
            trigger=TriggerType.EVENT,
            condition="task:*",
            agent_type="worker",
            config={},
        )
        node = MetaAgentNode(name="test_node", spawn_config=config)
        assert node.spawn_config is config

    def test_repr(self):
        node = MetaAgentNode(name="my_node", agent_id="a-1")
        r = repr(node)
        assert "MetaAgentNode" in r
        assert "my_node" in r
        assert "a-1" in r


class TestMetaAgentNodeStatus:
    def test_default_status_pending(self):
        node = MetaAgentNode(name="test")
        assert node.status == "pending"

    def test_status_running_after_execute(self):
        node = MetaAgentNode(name="test", agent_id="a-1")
        state = GraphState()
        # 直接调用 _execute_agent 模拟
        loop = asyncio.get_event_loop()
        loop.run_until_complete(node._execute_agent(state))
        # 注意：_execute_agent 不会改变 status，除非通过 execute()


class TestMetaAgentNodeExecute:
    @pytest.mark.asyncio
    async def test_execute_without_agent_id_no_spawner(self):
        """无 agent_id 且无 spawner 时，应该报错并设置 status=failed。"""
        node = MetaAgentNode(name="test_node")
        state = GraphState()
        state.input = "test input"

        result_state = await node.execute(state)

        assert node.status == "failed"
        assert len(result_state.errors) > 0
        assert "No agent_id available" in result_state.errors[0]

    @pytest.mark.asyncio
    async def test_execute_with_existing_agent_id(self):
        """有 agent_id 时，正常执行并更新 subgraph_results。"""
        node = MetaAgentNode(name="test_node", agent_id="agent-existing")
        state = GraphState()
        state.input = "test input"

        result_state = await node.execute(state)

        assert node.status == "completed"
        assert node.result is not None
        assert "agent_id" in result_state.subgraph_results["test_node"]
        assert result_state.subgraph_results["test_node"]["agent_id"] == "agent-existing"

    @pytest.mark.asyncio
    async def test_execute_updates_current_node(self):
        node = MetaAgentNode(name="node_abc", agent_id="a-1")
        state = GraphState()

        await node.execute(state)

        assert state.current_node == "node_abc"

    @pytest.mark.asyncio
    async def test_execute_records_metadata(self):
        node = MetaAgentNode(name="meta_node", agent_id="a-1")
        state = GraphState()

        await node.execute(state)

        key = "meta_node_status"
        assert key in state.metadata
        assert state.metadata[key] == "completed"


class TestMetaAgentNodeCancel:
    @pytest.mark.asyncio
    async def test_cancel_running_node(self):
        node = MetaAgentNode(name="test_node", agent_id="a-1")
        node.status = "running"
        node._cancel_event = asyncio.Event()

        # 开始一个长时间运行的任务
        async def long_task():
            await asyncio.sleep(10)

        task = asyncio.create_task(long_task())
        node._execution_task = task

        await node.cancel()

        assert node.status == "failed"
        assert task.done()

    @pytest.mark.asyncio
    async def test_cancel_idle_node(self):
        node = MetaAgentNode(name="test_node")
        node.status = "pending"
        # 无 agent_id 的节点无法真正执行，但 cancel 应该不报错
        await node.cancel()
        assert node.status == "pending"  # 已经是 pending，不会变


class TestMetaAgentNodeGetStatus:
    def test_get_status_returns_dict(self):
        node = MetaAgentNode(name="my_node", agent_id="a-99")
        node.status = "running"
        node.result = {"output": "test"}

        status = node.get_status()

        assert isinstance(status, dict)
        assert status["name"] == "my_node"
        assert status["agent_id"] == "a-99"
        assert status["status"] == "running"
        assert status["result"] == {"output": "test"}

    def test_get_status_before_execution(self):
        node = MetaAgentNode(name="test")
        status = node.get_status()
        assert status["name"] == "test"
        assert status["agent_id"] == ""
        assert status["status"] == "pending"
        assert status["result"] is None


class TestMetaAgentNodeWithSpawner:
    @pytest.mark.asyncio
    async def test_execute_with_spawner_and_config(self):
        """有 spawner 和 spawn_config 时，应自动创建 agent。"""
        mock_manager = MagicMock()
        mock_manager.create_subagent = AsyncMock(return_value={"id": "spawned-agent-1"})

        spawner = ConditionalSpawner(mock_manager)
        spawner.register(
            SpawnConfig(
                trigger=TriggerType.EVENT,
                condition="task:start",
                agent_type="worker",
                config={"name": "w1"},
            )
        )

        node = MetaAgentNode(
            name="spawn_node",
            spawn_config=SpawnConfig(
                trigger=TriggerType.EVENT,
                condition="task:start",
                agent_type="worker",
                config={"name": "w1"},
            ),
            spawner=spawner,
        )

        state = GraphState()
        # 注入 event 到 state.context，模拟外部触发
        state.context["event"] = "task:start"

        result_state = await node.execute(state)

        # agent 应该被创建
        assert node.agent_id == "spawned-agent-1"
        assert node.status == "completed"

    @pytest.mark.asyncio
    async def test_cancel_sets_status_failed(self):
        """cancel() 将 status 设为 failed 并设置取消事件。"""
        node = MetaAgentNode(name="test_node", agent_id="a-1")
        node.status = "running"
        node._cancel_event = asyncio.Event()

        await node.cancel()

        assert node.status == "failed"
        assert node._cancel_event.is_set()
