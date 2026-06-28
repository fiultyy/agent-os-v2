"""P3 多 agent 编排集成终点 — /v1/orchestrate 语义固化。

固化 P3 三件套的语义:
1. **AgentWorkerNode 闭环** — 每个 worker spawn→run→teardown 完整跑一遍,
   区别于 P2 预存 agent 并行(只 run 不 spawn/teardown)。
2. **_build_multi_agent_graph 拓扑** — multi_agent(ParallelNode)→fan_in
   (FanInNode aggregate)→synthesizer(run_agent_turn(orchestrator))。
3. **/v1/orchestrate 端点 + SSE** — fan-out/fan-in/synthesizer 节点可见。
4. **零 memory 调用(R1)** — 分支 subagent 全程零 memory;记忆只在 fan-in 后
   主 agent(orchestrator)单点(由其自身执行图负责,本端点不强制)。

pysqlite3 注入由 ``tests/conftest.py`` 统一处理,本文件不重复 patch。
"""

from __future__ import annotations

import ast
import inspect

import pytest

from src.api.routes import orchestrate as orch_mod
from src.api.models import OrchestrateRequest
from src.orchestration.agent_worker_node import AgentWorkerNode
from src.orchestration.multi_agent_graph import _build_multi_agent_graph
from src.services import _state


# ── Stub 装配 ────────────────────────────────────────────────────────


class _ScriptedLLM:
    """按 system_prompt 标记路由响应的 LLMClient 替身。

    每个 subagent role 在 config.system_prompt 里埋一个标记(如
    ``[role_a]``),synthesizer 综合调用的 user content 含 "Synthesize"。
    """

    def __init__(self, responses: dict[str, str]):
        self._resp = dict(responses)
        self.calls: list[tuple[str | None, list[dict]]] = []

    async def chat(self, messages, model=None, **kw):
        system = ""
        user = ""
        for m in messages:
            if m.get("role") == "system":
                system = m.get("content", "")
            elif m.get("role") == "user":
                user = m.get("content", "")
        self.calls.append((model, list(messages)))
        for key, resp in self._resp.items():
            if key in system:
                return resp
        if "Synthesize" in user or "synthesize" in user:
            return "SYNTH-RESULT"
        return "default-response"


class _RecordingAgentManager:
    """记录 create_subagent / teardown_subagent 调用的 in-memory 替身。

    闭环证据:create_subagent 次数 == teardown_subagent 次数 == 角色数,
    且每个 spawn 出来的 id 都被 teardown。
    """

    def __init__(self):
        from src.services import agent_manager as am

        # 直接复用生产 agent_manager(P0 实现),仅记录调用。
        self._am = am
        self.spawn_calls: list[dict] = []
        self.teardown_calls: list[str] = []
        self.spawned_ids: list[str] = []

    async def create_subagent(self, *, agent_type, config, parent_id="", session_id=""):
        self.spawn_calls.append({
            "agent_type": agent_type, "config": dict(config),
            "parent_id": parent_id, "session_id": session_id,
        })
        result = await self._am.create_subagent(
            agent_type=agent_type, config=config,
            parent_id=parent_id, session_id=session_id,
        )
        sid = result["id"] if isinstance(result, dict) else result
        self.spawned_ids.append(sid)
        return result

    async def teardown_subagent(self, agent_id):
        self.teardown_calls.append(agent_id)
        return await self._am.teardown_subagent(agent_id)


@pytest.fixture
def orchestrate_state():
    """备份/还原 _state 关键字段,装配最小编排依赖。"""
    saved = {
        k: getattr(_state, k, None)
        for k in (
            "llm_client", "agents", "communication_bus",
            "concurrency_controller", "memory_event_bus", "meta_spawner",
        )
    }
    _state.agents = {
        "orchestrator": {
            "id": "orchestrator",
            "model": "mo",
            "system_prompt": "[orchestrator] you synthesize",
            "status": "idle",
        },
    }
    _state.llm_client = _ScriptedLLM({
        "[role_a]": "RESP-A",
        "[role_b]": "RESP-B",
    })

    # communication_bus: 真实实例(broadcast 需 register_agent)。
    from src.communication.bus import CommunicationBus
    _state.communication_bus = CommunicationBus()

    # concurrency_controller: 真实实例(acquire/release_agent_slot)。
    from src.concurrency.controller import ConcurrencyController
    _state.concurrency_controller = ConcurrencyController(max_agents=8)

    yield

    for k, v in saved.items():
        setattr(_state, k, v)


# ── 1. AgentWorkerNode 闭环(spawn → run → teardown)──────────────────


@pytest.mark.asyncio
async def test_agent_worker_node_closed_loop_spawn_run_teardown(orchestrate_state):
    """AgentWorkerNode 跑一遍 create_subagent → run_agent_turn → teardown。

    证据:
    - spawn_calls 长度 == 1(role_a),parent_id == orchestrator。
    - teardown_calls == [spawned_id](finally 必跑,无泄漏)。
    - parallel_results[branch_role_a] 含 output == "RESP-A"。
    - subagent 已从 _state.agents 移除(teardown 生效,is_subagent 守卫通过)。
    """
    from src.graph import GraphState

    am = _RecordingAgentManager()
    worker = AgentWorkerNode(
        name="branch_role_a",
        role="role_a",
        agent_manager=am,
        config={"model": "ma", "system_prompt": "[role_a] you are A"},
        orchestrator_id="orchestrator",
        session_id="s1",
        turn_input="do A",
    )
    state = GraphState(input="do A", agent_id="orchestrator", session_id="s1")

    result = await worker.execute(state)

    assert worker.status == "completed", worker.status
    # spawn 证据
    assert len(am.spawn_calls) == 1
    assert am.spawn_calls[0]["agent_type"] == "role_a"
    assert am.spawn_calls[0]["parent_id"] == "orchestrator"
    spawned = am.spawned_ids[0]
    # teardown 证据(finally 必跑)
    assert am.teardown_calls == [spawned]
    # output 写入 parallel_results
    entry = state.parallel_results["branch_role_a"][0]
    assert entry["output"] == "RESP-A"
    assert entry["role"] == "role_a"
    assert entry["subagent_id"] == spawned
    # subagent 已销毁(is_subagent 守卫通过 → 真删)
    assert spawned not in _state.agents


@pytest.mark.asyncio
async def test_agent_worker_node_teardown_runs_on_failure(orchestrate_state):
    """create_subagent 成功但 run_agent_turn 返回 error 字符串(降级不 raise)时,
    teardown 仍在 finally 跑(无泄漏),状态 completed 但 output 带 error 标记。

    run_agent_turn 对 LLM 异常是 *降级*(返回 error 字符串,不 raise),所以
    worker 的 status 是 completed —— 但 teardown 仍由 finally 兜底执行。本测试
    即验证 finally 兜底:LLM 异常下 spawned id 仍被 teardown。
    """
    from src.graph import GraphState

    am = _RecordingAgentManager()
    worker = AgentWorkerNode(
        name="branch_x",
        role="role_x",
        agent_manager=am,
        config={"system_prompt": "[role_a] x"},
        orchestrator_id="orchestrator",
        session_id="s1",
    )
    # 让 llm.chat 抛异常 → run_agent_turn 降级返回 error 字符串(不 raise)。
    bad_llm = _ScriptedLLM({})

    async def boom(*a, **k):
        raise RuntimeError("LLM down")

    bad_llm.chat = boom  # type: ignore[assignment]
    _state.llm_client = bad_llm

    state = GraphState(input="x", agent_id="orchestrator", session_id="s1")
    await worker.execute(state)

    # LLM 异常被 run_agent_turn 降级 → worker status completed(不是 failed);
    # 但关键:spawned id 仍被 teardown(finally 兜底,无泄漏)。
    assert len(am.spawn_calls) == 1
    assert am.teardown_calls == am.spawned_ids, (
        "teardown must run in finally even when the run degrades"
    )
    assert am.spawned_ids[0] not in _state.agents
    # output 是降级 error 字符串(run_agent_turn 的契约)。
    entry = state.parallel_results["branch_x"][0]
    assert "[run_agent_turn error]" in entry["output"]


# ── 2. _build_multi_agent_graph 拓扑 ────────────────────────────────


@pytest.mark.asyncio
async def test_build_multi_agent_graph_topology_and_fan_out_fan_in(orchestrate_state):
    """multi_agent(ParallelNode)→fan_in(aggregate)→synthesizer 三段拓扑。

    证据:
    - 跑完后 fan_out 有 N 个角色结果(parallel_results['multi_agent'])。
    - fan_in 后 state.output 含所有分支 output(aggregate)。
    - synthesizer 后 state.output == "SYNTH-RESULT"(综合轮)。
    - spawn/teardown 次数 == 角色数(每个 worker 自管生命周期)。
    """
    am = _RecordingAgentManager()
    spec = [
        {"role": "role_a", "config": {"system_prompt": "[role_a] A"}},
        {"role": "role_b", "config": {"system_prompt": "[role_b] B"}},
    ]
    graph = _build_multi_agent_graph(
        orchestrator_id="orchestrator",
        sub_agents_spec=spec,
        input="task",
        session_id="s1",
        agent_manager=am,
    )
    from src.graph import GraphState

    state = GraphState(input="task", agent_id="orchestrator", session_id="s1")
    final = await graph.run(state)

    # fan-out:2 个角色都 spawn + teardown。
    assert len(am.spawn_calls) == 2
    assert sorted(c["agent_type"] for c in am.spawn_calls) == ["role_a", "role_b"]
    assert sorted(am.teardown_calls) == sorted(am.spawned_ids)
    # 所有 subagent 销毁。
    for sid in am.spawned_ids:
        assert sid not in _state.agents

    # fan_in aggregate:output 含 RESP-A / RESP-B。
    fan_out = final.parallel_results.get("multi_agent", [])
    outputs = {b.get("output") for b in fan_out}
    assert "RESP-A" in outputs and "RESP-B" in outputs
    # synthesizer 综合轮:最终 output == SYNTH-RESULT(用 orchestrator 跑)。
    assert final.output == "SYNTH-RESULT"
    # 综合轮的 LLM 调用次数 == 1(只 orchestrator 一点)。
    synth_calls = [
        c for c in _state.llm_client.calls
        if any("Synthesize" in (m.get("content", "")) for m in c[1])
    ]
    assert len(synth_calls) == 1


@pytest.mark.asyncio
async def test_build_multi_agent_graph_zero_memory_calls(orchestrate_state):
    """R1:_build_multi_agent_graph 函数体(去 docstring)零 memory *调用*。

    用与 P2 test_build_parallel_graph_has_zero_memory_calls 同样的精确正则
    (``memory_event_bus.emit`` / ``_trigger_*`` / ``memory_service.``),只匹配
    真实调用点 —— 函数 docstring/注释里的 "memory_event_bus" 字样不算违规。
    """
    import re

    src = inspect.getsource(_build_multi_agent_graph)
    tree = ast.parse(src)
    fn = tree.body[0]
    assert isinstance(fn, ast.FunctionDef)
    body = list(fn.body)
    # 剥离 docstring(首个字符串 Expr)。
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        body = body[1:]
    code = "\n".join(ast.get_source_segment(src, s) or "" for s in body)
    forbidden = re.findall(
        r"memory_event_bus\.emit|_trigger_ingest|_trigger_kg|memory_service\.|init_agent_blocks",
        code,
    )
    assert forbidden == [], (
        f"R1 violated: _build_multi_agent_graph body must have zero memory "
        f"calls, found {forbidden}"
    )


def test_build_multi_agent_graph_reuses_run_agent_turn():
    """静态:分支经 AgentWorkerNode(内含 run_agent_turn),synthesizer 复用
    run_agent_turn —— 不新写 LLM 调用路径。"""
    src = inspect.getsource(_build_multi_agent_graph)
    assert "run_agent_turn" in src, "synthesizer must reuse run_agent_turn"
    assert "AgentWorkerNode" in src, "branches must be AgentWorkerNode"
    # 不应直接调 llm_client.chat(那是绕过 run_agent_turn 的新路径)。
    assert "llm_client.chat" not in src


# ── 3. /v1/orchestrate 端点 + SSE 事件序列 ──────────────────────────


def _drain_sse(body: str) -> list[dict]:
    """Parse an SSE stream body into a list of {event, data} dicts."""
    events = []
    cur_event = None
    for line in body.split("\n"):
        if line.startswith("event: "):
            cur_event = line[len("event: "):].strip()
        elif line.startswith("data: ") and cur_event is not None:
            import json
            events.append({"event": cur_event, "data": json.loads(line[6:])})
            cur_event = None
    return events


@pytest.mark.asyncio
async def test_orchestrate_endpoint_sse_sequence(orchestrate_state):
    """/v1/orchestrate 端点 SSE 事件序列可见(multi_agent/fan_in/synthesizer)。

    直接 await 路由函数 + 消费 ``StreamingResponse.body_iterator``(与 P2
    test_execute_parallel_streams_all_nodes 同模式,绕开 TestClient/httpx 解码)。
    断言事件序列含 multi_agent(node_complete)+ fan_in + synthesizer +
    execution_complete,且 branches 含两个角色 + 每个有 subagent_id(spawn 证据)。
    """
    sse_events: list[str] = []

    req = OrchestrateRequest(
        orchestrator_agent_id="orchestrator",
        sub_agents=[
            {"role": "role_a", "config": {"system_prompt": "[role_a] A"}},
            {"role": "role_b", "config": {"system_prompt": "[role_b] B"}},
        ],
        input="task",
        session_id="sess-1",
    )
    resp = await orch_mod.orchestrate(req)
    # StreamingResponse — 逐块消费 body(与 P2 同模式)。
    async for chunk in resp.body_iterator:
        sse_events.append(chunk if isinstance(chunk, str) else chunk.decode())

    blob = "".join(sse_events)
    events = _drain_sse(blob)
    by_event: dict[str, list[dict]] = {}
    for e in events:
        by_event.setdefault(e["event"], []).append(e["data"])

    # multi_agent 节点 complete,branches 含 role_a + role_b
    multi_complete = [
        d for d in by_event.get("node_complete", [])
        if d.get("node") == "multi_agent"
    ]
    assert multi_complete, "multi_agent node_complete missing"
    roles = {b["role"] for b in multi_complete[0]["branches"]}
    assert roles == {"role_a", "role_b"}
    # 每个 branch output 非空(fan-out 真跑证据;spawn→run→teardown 闭环由
    # 专门的单测 test_agent_worker_node_closed_loop_spawn_run_teardown 固化)。
    for b in multi_complete[0]["branches"]:
        assert b["output"] in ("RESP-A", "RESP-B"), (
            f"branch {b.get('role')} output missing: {b.get('output')!r}"
        )

    # fan_in + synthesizer 节点可见(SSE blob 含节点名)。
    assert "fan_in" in blob, "fan_in not surfaced in SSE"
    assert "synthesizer" in blob, "synthesizer not surfaced in SSE"

    # execution_complete,SSE 流末端。
    assert "execution_complete" in by_event
    final = by_event["execution_complete"][0]
    assert final["output"] == "SYNTH-RESULT"
    assert set(final["roles"]) == {"role_a", "role_b"}

    # orchestrator 状态最终回 idle。
    statuses = [d["status"] for d in by_event["agent_status"]]
    assert "running" in statuses and "idle" in statuses


@pytest.mark.asyncio
async def test_orchestrate_endpoint_rejects_unknown_orchestrator(orchestrate_state):
    """orchestrator_agent_id 不在 _state.agents → 404 JSONResponse。"""
    req = OrchestrateRequest(
        orchestrator_agent_id="nope",
        sub_agents=[{"role": "role_a"}],
        input="x",
    )
    resp = await orch_mod.orchestrate(req)
    # 校验路径早返回 JSONResponse(404),非 StreamingResponse。
    assert resp.status_code == 404
    assert resp.body  # JSONResponse has a body


@pytest.mark.asyncio
async def test_orchestrate_endpoint_rejects_empty_sub_agents(orchestrate_state):
    """sub_agents 为空 → 400 JSONResponse。"""
    req = OrchestrateRequest(
        orchestrator_agent_id="orchestrator",
        sub_agents=[],
        input="x",
    )
    resp = await orch_mod.orchestrate(req)
    assert resp.status_code == 400
    assert resp.body


# ── 4. 零 memory 调用(R1)— orchestrate 路径不触发记忆事件 ─────────


@pytest.mark.asyncio
async def test_orchestrate_no_memory_emit_on_branches(orchestrate_state):
    """orchestrate 路径不 emit memory 事件(分支 subagent 零 memory)。

    装一个哨兵 event_bus 到 _state.memory_event_bus,统计 emit 次数。
    /v1/orchestrate 端点体不应经 memory_event_bus.emit(与 /execute 不同)。
    直接 await 路由函数 + 消费 body_iterator。
    """
    emitted = []

    class _SentinelBus:
        async def emit(self, *a, **k):
            emitted.append((a, k))

        def set_enabled(self, *a, **k):
            pass

    _state.memory_event_bus = _SentinelBus()

    req = OrchestrateRequest(
        orchestrator_agent_id="orchestrator",
        sub_agents=[
            {"role": "role_a", "config": {"system_prompt": "[role_a] A"}},
        ],
        input="task",
    )
    resp = await orch_mod.orchestrate(req)
    async for chunk in resp.body_iterator:
        # 消费完整流以驱动 run_graph。
        pass

    # orchestrate 端点不 emit memory 事件(零 memory 调用 — R1)。
    assert emitted == [], (
        f"R1 violated: /v1/orchestrate must not emit memory events, got {emitted}"
    )
