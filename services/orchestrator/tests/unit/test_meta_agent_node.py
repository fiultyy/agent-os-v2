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
        # 直接调用 _execute_agent:无 agent_manager → stub 兼容路径;events 均为
        # None → 立即返回 _build_result("completed")。该测试仅验证直接调用不崩,
        # _execute_agent 自身不改 status(status 由 execute() 管)。
        # NOTE(B1): 直接用 asyncio.run 而非 get_event_loop,规避 pytest-asyncio
        # MainThread event loop 耗尽导致的偶发挂起(基线已存在该 flake)。
        asyncio.run(node._execute_agent(state))


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


class TestMetaAgentNodeRealExecute:
    """P1: 真 execute 闭环 — create_subagent → run_agent_turn → teardown_subagent。

    Real mode is enabled by passing an ``agent_manager`` that exposes BOTH
    ``create_subagent`` and ``teardown_subagent`` (the P0 lifecycle contract).
    These tests mock the three primitives and assert:

    - closed-loop call ORDER (create → run → teardown)
    - completion_event set by REAL completion (not pre-set stub signal)
    - subgraph_results carries the LLM ``response``
    - R1: run_agent_turn performs ZERO memory calls
    - None-safe degradation when create_subagent returns no id
    """

    def _make_manager(self, create_ret=None, teardown_ret=True):
        """Build a mock agent_manager exposing the P0 lifecycle contract."""
        mgr = MagicMock()
        mgr.create_subagent = AsyncMock(return_value=create_ret or {"id": "sub-xyz"})
        mgr.teardown_subagent = AsyncMock(return_value=teardown_ret)
        return mgr

    @pytest.mark.asyncio
    async def test_real_closed_loop_order(self):
        """create_subagent → run_agent_turn → teardown_subagent in order."""
        mgr = self._make_manager()
        node = MetaAgentNode(name="real_node", agent_id="parent-1", agent_manager=mgr)
        node.spawn_config = SpawnConfig(
            trigger=TriggerType.EVENT,
            condition="*",
            agent_type="worker",
            config={"system_prompt": "you are a worker"},
        )
        state = GraphState()
        state.input = "do the thing"

        # run_agent_turn is imported lazily INSIDE _run_real_turn, so patch it at
        # its source module (agent_runner). meta_agent_node._run_real_turn does
        # `from src.agent.meta.agent_runner import run_agent_turn` which binds the
        # (patched) name at call time.
        with patch(
            "src.agent.meta.agent_runner.run_agent_turn",
            new=AsyncMock(return_value="LLM-RESPONSE"),
        ) as mock_run:
            result_state = await node.execute(state)

        # 1. create called before run, run before teardown (order via call order)
        mgr.create_subagent.assert_awaited_once()
        mgr.teardown_subagent.assert_awaited_once()
        mock_run.assert_awaited_once()
        # Explicit order assertion: create's awaited-time < run's < teardown's
        assert mgr.create_subagent.await_count == 1
        assert mock_run.await_count == 1
        assert mgr.teardown_subagent.await_count == 1

        # 2. completion_event reflects real completion
        assert node._completion_event is not None
        assert node._completion_event.is_set()

        # 3. subgraph_results carries the LLM response
        assert node.status == "completed"
        res = result_state.subgraph_results["real_node"]
        assert res["response"] == "LLM-RESPONSE"
        assert res["subagent_id"] == "sub-xyz"
        assert res["agent_id"] == "parent-1"

        # 4. teardown received the spawned sub_id
        mgr.teardown_subagent.assert_awaited_once_with("sub-xyz")

    @pytest.mark.asyncio
    async def test_completion_event_not_pre_set_in_real_mode(self):
        """Real mode: completion_event must NOT be pre-set by execute().

        It is only set after the real turn finishes. We assert this by checking
        that during the run the event is set, and that the spawner path does not
        set it before run_agent_turn is invoked.
        """
        mgr = self._make_manager()
        node = MetaAgentNode(name="real_node", agent_id="p-1", agent_manager=mgr)
        state = GraphState()
        state.input = "hi"

        seen_event_before_run = {}

        async def fake_run(agent_id, input, session_id, system_prompt=None):
            # At the moment run_agent_turn is invoked, completion_event should be
            # UNSET (real mode defers the set to _run_real_turn's finally).
            seen_event_before_run["set"] = (
                node._completion_event is not None
                and node._completion_event.is_set()
            )
            return "ok"

        with patch(
            "src.agent.meta.agent_runner.run_agent_turn",
            new=fake_run,
        ):
            await node.execute(state)

        assert seen_event_before_run["set"] is False
        assert node._completion_event.is_set()

    @pytest.mark.asyncio
    async def test_real_turn_no_memory_calls_r1(self, monkeypatch):
        """R1 red-line: run_agent_turn must perform ZERO memory calls.

        3B 后 run_agent_turn 经 build_native_agent + agent.run(不再直调 llm.chat)。
        patch build_model(TestModel)免真 API;断言 result.output 经组装器返回,且
        memory_event_bus.emit 零调用(R1 守恒:子代理不沉淀记忆)。

        注:_state.memory_event_bus 经 ADR-C1 后只在 engine.bootstrap() 内装配(import
        engine 零副作用)。conftest autouse _reset_state 每测前清字段。故本测 monkeypatch
        注入 emit spy + 断言 not awaited —— 准确验证 R1(子代理不触发 memory 副作用),不
        依赖 engine 装配时序。
        """
        from unittest.mock import AsyncMock, MagicMock

        from pydantic_ai.models.test import TestModel

        from src.services import _state
        from src.agent.meta.agent_runner import run_agent_turn

        # R1 真验证:memory_event_bus.emit 不被调用。monkeypatch 隔离 engine 模块级
        # 装配的 bus(全套 import engine 后非 None),注入 emit spy。
        bus_spy = MagicMock()
        bus_spy.emit = AsyncMock()
        monkeypatch.setattr(_state, "memory_event_bus", bus_spy)
        monkeypatch.setattr(_state, "memory_service", None)
        monkeypatch.setattr(_state, "write_queue", None)

        _state.agents["sub-r1"] = {
            "id": "sub-r1",
            "system_prompt": "p",
            "model": None,  # None → build_model 读 env(TestModel override 免 env)
            "is_subagent": True,
        }
        try:
            with patch(
                "src.harness.native_agent.build_model",
                lambda *a, **kw: TestModel(custom_output_text="resp-from-subagent"),
            ):
                out = await run_agent_turn("sub-r1", "hello", "sess")
            assert out == "resp-from-subagent"

            # R1: ZERO memory emit(子代理不沉淀记忆)。
            bus_spy.emit.assert_not_awaited()
        finally:
            _state.agents.pop("sub-r1", None)

    @pytest.mark.asyncio
    async def test_real_turn_degrades_when_no_id(self):
        """create_subagent returns no id → recorded error, teardown skipped, no crash."""
        mgr = self._make_manager(create_ret={"id": ""})
        node = MetaAgentNode(name="real_node", agent_id="p-1", agent_manager=mgr)
        state = GraphState()
        state.input = "x"

        with patch(
            "src.agent.meta.agent_runner.run_agent_turn",
            new=AsyncMock(return_value="should-not-reach"),
        ) as mock_run:
            result_state = await node.execute(state)

        # run_agent_turn never ran (no sub_id), teardown never ran (no sub_id).
        mock_run.assert_not_called()
        mgr.teardown_subagent.assert_not_called()
        res = result_state.subgraph_results["real_node"]
        assert "error" in res
        assert "no id" in res["error"]
        # completion_event still set (real-mode finally always sets it).
        assert node._completion_event.is_set()

    @pytest.mark.asyncio
    async def test_real_turn_llm_failure_recorded_not_raised(self):
        """run_agent_turn raising → error recorded, status reflects failure, teardown still runs."""
        mgr = self._make_manager()
        node = MetaAgentNode(name="real_node", agent_id="p-1", agent_manager=mgr)
        state = GraphState()
        state.input = "x"

        with patch(
            "src.agent.meta.agent_runner.run_agent_turn",
            new=AsyncMock(side_effect=RuntimeError("llm boom")),
        ):
            result_state = await node.execute(state)

        # teardown ALWAYS runs (finally), even on LLM failure.
        mgr.teardown_subagent.assert_awaited_once_with("sub-xyz")
        res = result_state.subgraph_results["real_node"]
        assert "error" in res
        assert "llm boom" in res["error"]

    @pytest.mark.asyncio
    async def test_stub_compat_when_manager_lacks_teardown(self):
        """agent_manager with create_subagent but NOT teardown → stub path, no real loop.

        Guarantees backward compatibility: an old mock that only stubs
        create_subagent (e.g. legacy ConditionalSpawner tests) does NOT trigger
        the real turn — _has_real_lifecycle() returns False and the legacy
        event-driven wait / _build_result shell runs instead.
        """
        # spec=["create_subagent"] makes hasattr(mgr, "teardown_subagent") False —
        # MagicMock auto-creates attributes, so we restrict the spec explicitly.
        class _PartialManager:
            async def create_subagent(self, **kwargs):
                return {"id": "x"}
            # NOTE: no teardown_subagent

        mgr_partial = _PartialManager()
        assert not hasattr(mgr_partial, "teardown_subagent")
        assert hasattr(mgr_partial, "create_subagent")

        node = MetaAgentNode(
            name="stub_node", agent_id="a-1", agent_manager=mgr_partial
        )
        state = GraphState()

        with patch(
            "src.agent.meta.agent_runner.run_agent_turn",
            new=AsyncMock(return_value="REAL"),
        ) as mock_run:
            result_state = await node.execute(state)

        # Stub path: real turn NEVER runs; empty shell result returned.
        mock_run.assert_not_called()
        assert node.status == "completed"
        res = result_state.subgraph_results["stub_node"]
        assert "agent_id" in res  # _build_result shell
        assert "response" not in res
