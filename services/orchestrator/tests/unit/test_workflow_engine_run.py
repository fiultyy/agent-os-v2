"""W-P0-3 单测:WorkflowEngine.run(fan-out + fan-in 单点聚合)+ _emit_workflow。

verify(design §5 W-P0-3):
- mock build_native_agent 返 3 fake agent 输出 "a"/"b"/raise → run 返 3 NodeResult
  (2 success 1 error),total_usage 累加成功 2 个(不含 error node 的零 usage)
- fan_in='merge' 时 dict 字段合并(success only,later-wins)
- fan_in='list' 时原样数组(success/error 混杂)
- R7:raise 的 fake agent 经 gather(return_exceptions=True)被 _spawn_agent 降级为
  NodeResult(status='error') 而非冒泡(run 本身不 raise)
- RK2:total_usage = sum(per-node usage),per-node usage 是独立对象
- R4:_emit_workflow wire tick_completed,emitter=None 不崩
- R3:emit 招回抛异常不冒泡(fire-and-forget)
"""

import asyncio

import harness.workflow_engine as wf_mod
from harness.workflow_engine import (
    WORKFLOW_FLOW_EVENTS,
    WorkflowContext,
    WorkflowEngine,
    WorkflowNodesSpec,
)
from pydantic_ai.usage import RunUsage

# ── sync wrapper(避开 pytest-asyncio loop pollution,见
#    feedback-pytest-asyncio-loop-pollution + spawn 测试母版)──
_LOOP = None


def run_async(coro):
    global _LOOP
    if _LOOP is None or _LOOP.is_closed():
        _LOOP = asyncio.new_event_loop()
        asyncio.set_event_loop(_LOOP)
    return _LOOP.run_until_complete(coro)


# ── Fake native Agent ─────────────────────────────────────────────────
class _FakeRunResult:
    def __init__(self, output):
        self.output = output


class _FakeAgent:
    """run 返固定 output 或 raise;记录传给 agent.run 的 usage 对象。"""

    def __init__(self, output="ok", exc=None):
        self._output = output
        self._exc = exc
        self.run_usage = None

    async def run(self, task_input, *, usage=None, usage_limits=None):
        if usage is not None:
            # 模拟 LLM 调用产生的 token 用量(对 per-node RunUsage 累加)
            usage.requests = (usage.requests or 0) + 1
            usage.input_tokens = (usage.input_tokens or 0) + 10
            usage.output_tokens = (usage.output_tokens or 0) + 5
            self.run_usage = usage
        if self._exc is not None:
            raise self._exc
        return _FakeRunResult(self._output)


# ── monkeypatch helper ────────────────────────────────────────────────
def _patch_build(agents):
    """返 (recorder, restore)。agents 是 list,每次 build 弹一个。"""
    it = iter(agents)
    captured = {"count": 0, "calls": []}
    orig = wf_mod.build_native_agent

    def _fake(*a, **kw):
        captured["count"] += 1
        captured["calls"].append(kw)
        return next(it)

    wf_mod.build_native_agent = _fake

    def restore():
        wf_mod.build_native_agent = orig

    return captured, restore


def _ctx(concurrency=8):
    return WorkflowContext(
        session_id="s1", agent_id_prefix="wf", run_id="wf_run1",
        concurrency=concurrency,
    )


def _spec(nodes_dicts, fan_in="list"):
    return WorkflowNodesSpec.model_validate(
        {"nodes": nodes_dicts, "fan_in": fan_in}
    )


# ─────────────────────────────────────────────────────────────────────
# 主断言:3 fake agent("a"/"b"/raise)→ 3 NodeResult(2 success 1 error)
# ─────────────────────────────────────────────────────────────────────
def test_run_3_nodes_2_success_1_error():
    agents = [_FakeAgent(output="a"), _FakeAgent(output="b"), _FakeAgent(exc=RuntimeError("boom"))]
    cap, restore = _patch_build(agents)
    try:
        e = WorkflowEngine()
        spec = _spec([{"prompt": "t1"}, {"prompt": "t2"}, {"prompt": "t3"}])
        result = run_async(e.run(spec, _ctx()))
    finally:
        restore()

    assert cap["count"] == 3  # 3 个 node 各 spawn 一次
    assert result.node_count == 3
    assert len(result.node_results) == 3

    statuses = [nr.status for nr in result.node_results]
    assert statuses.count("success") == 2
    assert statuses.count("error") == 1

    outputs = [nr.output for nr in result.node_results if nr.status == "success"]
    assert sorted(outputs) == ["a", "b"]

    # run 整体 status='success'(至少一个 success)
    assert result.status == "success"
    # run_id 透传
    assert result.run_id == "wf_run1"
    # elapsed_ms 非负
    assert result.elapsed_ms >= 0


# ─────────────────────────────────────────────────────────────────────
# RK2:total_usage = sum(per-node success usage);error node usage 零
# ─────────────────────────────────────────────────────────────────────
def test_run_total_usage_aggregates_success_only():
    agents = [_FakeAgent(output="a"), _FakeAgent(output="b"), _FakeAgent(exc=RuntimeError("x"))]
    cap, restore = _patch_build(agents)
    try:
        e = WorkflowEngine()
        spec = _spec([{"prompt": "t1"}, {"prompt": "t2"}, {"prompt": "t3"}])
        result = run_async(e.run(spec, _ctx()))
    finally:
        restore()

    # 2 success node 各 +1 request / +10 input / +5 output;error node 不计
    assert result.total_usage.requests == 2
    assert result.total_usage.input_tokens == 20
    assert result.total_usage.output_tokens == 10


def test_run_per_node_usage_independent_objects():
    """RK2:per-node RunUsage 是独立对象,fan-in 阶段在 Lock 内 +=。"""
    agents = [_FakeAgent(output="a"), _FakeAgent(output="b")]
    _, restore = _patch_build(agents)
    try:
        e = WorkflowEngine()
        spec = _spec([{"prompt": "t1"}, {"prompt": "t2"}])
        run_async(e.run(spec, _ctx()))
    finally:
        restore()
    # 两 fake agent 各自记录的 usage 对象不同(per-node)
    assert agents[0].run_usage is not agents[1].run_usage


# ─────────────────────────────────────────────────────────────────────
# F2:fan_in='list' 时 WorkflowResult.merged_output=None / errors 聚合
# (原 _fan_in helper 语义保留 — helper 仍返原样数组,但 run 不再把数组挂字段;
#  消费者读 node_results[i].output;errors 聚合 status!='success' node)
# ─────────────────────────────────────────────────────────────────────
def test_run_fan_in_list_merged_output_none_errors_aggregated():
    agents = [_FakeAgent(output="a"), _FakeAgent(exc=RuntimeError("boom")), _FakeAgent(output="c")]
    _, restore = _patch_build(agents)
    try:
        e = WorkflowEngine()
        spec = _spec(
            [{"prompt": "1"}, {"prompt": "2"}, {"prompt": "3"}],
            fan_in="list",
        )
        result = run_async(e.run(spec, _ctx()))
    finally:
        restore()

    # F2:list 模式 merged_output=None(消费者读 node_results 原样 output)
    assert result.merged_output is None
    # errors 聚合所有 status!='success' node 的 error 字符串
    assert result.errors == ["boom"]
    # node_results 仍保留原样(success output + error None 混杂)
    outputs = [nr.output for nr in result.node_results]
    assert outputs == ["a", None, "c"]


# ─────────────────────────────────────────────────────────────────────
# F2:fan_in='merge' 时 WorkflowResult.merged_output=合并 dict + errors 聚合
# (success-only 字段合并 later-wins;非 dict success output 跳过;errors 聚合)
# ─────────────────────────────────────────────────────────────────────
def test_run_fan_in_merge_merged_output_dict_later_wins():
    agents = [
        _FakeAgent(output={"a": 1, "shared": "first"}),
        _FakeAgent(exc=RuntimeError("boom")),
        _FakeAgent(output={"b": 2, "shared": "second"}),  # later-wins on 'shared'
    ]
    _, restore = _patch_build(agents)
    try:
        e = WorkflowEngine()
        spec = _spec(
            [{"prompt": "1"}, {"prompt": "2"}, {"prompt": "3"}],
            fan_in="merge",
        )
        result = run_async(e.run(spec, _ctx()))
    finally:
        restore()

    # F2:merged_output 是 success-only dict 字段合并(later-wins)
    assert result.merged_output == {"a": 1, "b": 2, "shared": "second"}
    # errors 聚合
    assert result.errors == ["boom"]


def test_run_fan_in_merge_skips_non_dict_success_output():
    """F2:success 但 output 非 dict(str)→ skip 不崩,merged 只含 dict 字段。"""
    agents = [
        _FakeAgent(output="not a dict"),  # success str → skip
        _FakeAgent(output={"x": 1}),
    ]
    _, restore = _patch_build(agents)
    try:
        e = WorkflowEngine()
        spec = _spec([{"prompt": "1"}, {"prompt": "2"}], fan_in="merge")
        result = run_async(e.run(spec, _ctx()))
    finally:
        restore()
    assert result.merged_output == {"x": 1}
    assert result.errors == []


# ─────────────────────────────────────────────────────────────────────
# _fan_in helper 语义仍直测(模块级纯函数,run 内部依赖)
# ─────────────────────────────────────────────────────────────────────
def test_fan_in_helper_list_returns_original_array():
    from harness.workflow_engine import _fan_in

    class _NR:
        def __init__(self, status, output, error=None):
            self.status = status
            self.output = output
            self.error = error
    out, errs = _fan_in(
        [_NR("success", "a"), _NR("error", None, "boom"), _NR("success", "c")],
        "list",
    )
    assert out == ["a", None, "c"]  # 原样含 error node 的 None
    assert errs == ["boom"]


# ─────────────────────────────────────────────────────────────────────
# R7 语义鸿沟:raise 的 fake agent 不让 run raise(gather return_exceptions)
# ─────────────────────────────────────────────────────────────────────
def test_run_gather_barrier_does_not_raise_on_node_failure():
    agents = [_FakeAgent(exc=RuntimeError("node1 boom")),
              _FakeAgent(output="ok")]
    _, restore = _patch_build(agents)
    try:
        e = WorkflowEngine()
        spec = _spec([{"prompt": "1"}, {"prompt": "2"}])
        # run 不 raise — gather(return_exceptions=True) BARRIER
        result = run_async(e.run(spec, _ctx()))
    finally:
        restore()
    assert result.node_count == 2
    assert any(nr.status == "error" for nr in result.node_results)
    assert any(nr.status == "success" for nr in result.node_results)


def test_run_all_nodes_error_overall_status_error():
    agents = [_FakeAgent(exc=RuntimeError("a")), _FakeAgent(exc=RuntimeError("b"))]
    _, restore = _patch_build(agents)
    try:
        e = WorkflowEngine()
        spec = _spec([{"prompt": "1"}, {"prompt": "2"}])
        result = run_async(e.run(spec, _ctx()))
    finally:
        restore()
    # 全 error → overall status='error'
    assert result.status == "error"
    assert all(nr.status == "error" for nr in result.node_results)


# ─────────────────────────────────────────────────────────────────────
# R3 + R4:_emit_workflow wire tick_completed / emitter=None 不崩
# ─────────────────────────────────────────────────────────────────────
def test_emit_workflow_no_crash_when_emitter_none():
    e = WorkflowEngine(emitter=None)
    # 不崩,静默 return(emitter=None 分支)
    e._emit_workflow("workflow_started", "wf_x", {"node_count": 2})


def test_emit_workflow_wires_tick_completed_and_flow_event():
    """R4:event_type 恒 tick_completed,真实语义塞 data.flow_event +
    data.flow_payload;emitter.emit 招回的 event dict 含正确字段。"""
    captured = []

    class _Emitter:
        async def emit(self, ev):
            captured.append(ev)

    e = WorkflowEngine(emitter=_Emitter())
    e._emit_workflow(
        "workflow_started", "wf_run1",
        {"node_count": 3}, session_id="sess42",
    )
    # create_task 调度 emit,需跑一轮 loop 让其完成
    run_async(asyncio.sleep(0.01))

    assert len(captured) == 1
    ev = captured[0]
    # R4:event_type 恒 tick_completed(observe enum 冻结)
    assert ev["event_type"] == "tick_completed"
    # 真实语义塞 data.flow_event + data.flow_payload
    assert ev["data"]["flow_event"] == "workflow_started"
    assert ev["data"]["flow_payload"] == {"node_count": 3}
    assert ev["data"]["flow_event"] in WORKFLOW_FLOW_EVENTS
    # session_id / run_id 透传
    assert ev["session_id"] == "sess42"
    assert ev["harness_id"] == "wf_run1"


def test_emit_workflow_fire_and_forget_does_not_raise():
    """R3:emitter.emit 抛异常不冒泡(fire-and-forget)。"""
    class _BadEmitter:
        async def emit(self, ev):
            raise RuntimeError("observe down")

    e = WorkflowEngine(emitter=_BadEmitter())
    # _emit_workflow 内部 try/except 兜底 — create_task 调度的 emit 抛异常
    # 走 loop exception handler,但 _emit_workflow 本身不 raise
    e._emit_workflow("workflow_completed", "wf_x", {"status": "success"})
    # 跑 loop 让 scheduled task 完成(异常进 loop exception handler,不冒泡到这里)
    run_async(asyncio.sleep(0.01))


# ─────────────────────────────────────────────────────────────────────
# 4 事件 emit 序:workflow_started / node_started×N / node_completed×N /
# workflow_completed(emitter 通电才捕获;这里验证事件名集合 + N 计数)
# ─────────────────────────────────────────────────────────────────────
def test_run_emits_4_event_kinds_with_node_multiplicity():
    captured = []

    class _Emitter:
        async def emit(self, ev):
            captured.append(ev["data"]["flow_event"])

    agents = [_FakeAgent(output="a"), _FakeAgent(exc=RuntimeError("x"))]
    _, restore = _patch_build(agents)
    try:
        e = WorkflowEngine(emitter=_Emitter())
        spec = _spec([{"prompt": "1"}, {"prompt": "2"}])
        run_async(e.run(spec, _ctx()))
    finally:
        restore()
    run_async(asyncio.sleep(0.02))  # flush create_task

    # workflow_started×1 + node_started×2 + node_completed×2 + workflow_completed×1 = 6
    assert captured.count("workflow_started") == 1
    assert captured.count("workflow_completed") == 1
    assert captured.count("workflow_node_started") == 2
    assert captured.count("workflow_node_completed") == 2
    # 全部事件名 ∈ WORKFLOW_FLOW_EVENTS(R4 冻结集合)
    assert all(name in WORKFLOW_FLOW_EVENTS for name in captured)
    # 起止序:started 在最前,completed 在最后
    assert captured[0] == "workflow_started"
    assert captured[-1] == "workflow_completed"


# ─────────────────────────────────────────────────────────────────────
# concurrency cap:Semaphore(N) 限并发(轻量验证 — 不测时序,只测不崩 +
# 结果完整;真并发限流测 deferred to integration)
# ─────────────────────────────────────────────────────────────────────
def test_run_concurrency_cap_completes_all_nodes():
    agents = [_FakeAgent(output=f"o{i}") for i in range(5)]
    _, restore = _patch_build(agents)
    try:
        e = WorkflowEngine()
        spec = _spec([{"prompt": str(i), "label": f"n{i}"} for i in range(5)])
        result = run_async(e.run(spec, WorkflowContext(
            session_id="s", agent_id_prefix="wf", run_id="wf_c",
            concurrency=2,
        )))
    finally:
        restore()
    assert result.node_count == 5
    assert all(nr.status == "success" for nr in result.node_results)


# ─────────────────────────────────────────────────────────────────────
# 未知 flow_event 名:_emit_workflow 不崩(仅 warning log,机械集合守恒松校验)
# ─────────────────────────────────────────────────────────────────────
def test_emit_workflow_unknown_event_name_no_crash():
    class _Emitter:
        async def emit(self, ev):
            pass
    e = WorkflowEngine(emitter=_Emitter())
    # unknown event name — 仅 warning,不崩
    e._emit_workflow("not_a_real_workflow_event", "wf_x", {})
    run_async(asyncio.sleep(0.01))


# ─────────────────────────────────────────────────────────────────────
# _usage_dict 序列化(payload 用)
# ─────────────────────────────────────────────────────────────────────
def test_usage_dict_serializes_runusage():
    from harness.workflow_engine import _usage_dict
    u = RunUsage()
    u.requests = 3
    u.input_tokens = 100
    u.output_tokens = 50
    d = _usage_dict(u)
    assert d == {
        "requests": 3,
        "input_tokens": 100,
        "output_tokens": 50,
        "total_tokens": (u.total_tokens or 0),
    }
