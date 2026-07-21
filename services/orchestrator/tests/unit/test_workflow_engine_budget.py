"""W-P1-2 单测:budget 强制(ctx.budget_limits + check_before_request 短路)。

verify(design §5 W-P1-2):
- 设 budget_limits=UsageLimits(total_tokens_limit=100),mock 子 agent 各耗 60 token
  → run 在第 2 个完成后,第 3 个 spawn 前 check_before_request(120>100) 触达
  UsageLimitExceeded → _spawn_agent 返 NodeResult(status='error',
  error='budget_exceeded') + ctx.abort.set() 广播,sibling _bounded 入口短路
- run 整体返 status='success'(有部分 success node)+ budget_exceeded=True
  (total_usage 标记 cap 触达)
- total_usage = 2 个完成 node 的累计(120),不含 budget_exceeded / skipped node
- RK3:_spawn_agent 调 agent.run(usage_limits=UsageLimits(request_limit=None))
  显式 unset request_limit(默认 50 不会提前触发 — 单测 mock 直接断言透传)

红线:R1/R2/R5 经 _spawn_agent 零增量负担(grep 机械守恒仍空);R3 fire-and-forget
budget 短路本身降级返 NodeResult(error) 不冒泡主路径。
"""

import asyncio

import harness.workflow_engine as wf_mod
from harness.workflow_engine import (
    WorkflowContext,
    WorkflowEngine,
    WorkflowNodesSpec,
)
from pydantic_ai.usage import RunUsage, UsageLimits

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
    """每次 run 累加 ``per_call_tokens`` 到 usage;记录传给 run 的 usage_limits。

    ``latency`` 模拟真实 LLM latency(给 run 加 ``await asyncio.sleep``),让出协程
    捕获 concurrency budget 竞态 — 无 await 时协程不 yield,等效串行,掩盖竞态
    (skeptic finding:旧 _FakeAgent.run 无 await 是假阳性根因)。
    """

    def __init__(self, output="ok", per_call_input_tokens=60, per_call_output_tokens=0, latency=0.0):
        self._output = output
        self._per_call_input = per_call_input_tokens
        self._per_call_output = per_call_output_tokens
        self._latency = latency
        self.captured_usage_limits = None

    async def run(self, task_input, *, usage=None, usage_limits=None):
        self.captured_usage_limits = usage_limits
        if self._latency > 0:
            await asyncio.sleep(self._latency)  # 让出协程 — 暴露 concurrency 竞态
        if usage is not None:
            usage.requests = (usage.requests or 0) + 1
            usage.input_tokens = (usage.input_tokens or 0) + self._per_call_input
            usage.output_tokens = (usage.output_tokens or 0) + self._per_call_output
        return _FakeRunResult(self._output)


# ── monkeypatch helper ────────────────────────────────────────────────
def _patch_build(agents):
    it = iter(agents)
    captured = {"count": 0}
    orig = wf_mod.build_native_agent

    def _fake(*a, **kw):
        captured["count"] += 1
        return next(it)

    wf_mod.build_native_agent = _fake

    def restore():
        wf_mod.build_native_agent = orig

    return captured, restore


def _ctx_with_budget(
    total_tokens_limit, concurrency=8,
):
    """W-P1-2 budget ctx。budget 强制经 ``budget_lock`` 串行根治 concurrency 竞态
    (skeptic finding #1/#2);``concurrency`` 在 budget 路径下 effective=1。"""
    return WorkflowContext(
        session_id="s1",
        agent_id_prefix="wf",
        run_id="wf_budget1",
        concurrency=concurrency,
        budget_limits=UsageLimits(total_tokens_limit=total_tokens_limit),
    )


def _spec(n_nodes):
    return WorkflowNodesSpec.model_validate(
        {"nodes": [{"prompt": f"t{i+1}", "label": f"n{i+1}"} for i in range(n_nodes)]}
    )


# ─────────────────────────────────────────────────────────────────────
# 主断言:3 子 agent × 60 token,total_limit=100 → 第 2 个完成后第 3 个短路
# (with latency=True:捕获 concurrency 竞态 — skeptic finding #1/#2 根治验证)
# ─────────────────────────────────────────────────────────────────────
def test_budget_short_circuits_after_cap_hit():
    """verify 字面:budget=100,3 子 agent 各耗 60 → 第 2 个完成后 break,
    status='success'(部分结果)+ budget_exceeded=True。

    concurrency=8 + latency=0.01(模拟真实 LLM latency)暴露竞态:旧实现无预留时
    8 sibling 同时读 stale total=0 全过 check → budget 被绕过(success=8/total=480)。
    W-P1-2 预留根治:budget_estimate=60 让 sibling 3 projected(0+60+60=120)>100 短路。
    """
    agents = [_FakeAgent(per_call_input_tokens=60, latency=0.01) for _ in range(3)]
    cap, restore = _patch_build(agents)
    try:
        e = WorkflowEngine()
        spec = _spec(3)
        result = run_async(e.run(spec, _ctx_with_budget(total_tokens_limit=100)))
    finally:
        restore()

    # 前 2 个 node 完成(各 60 token,累计 120);第 3 个 check_before_request
    # (projected=120>100)触达 → 短路 skipped
    success_nrs = [nr for nr in result.node_results if nr.status == "success"]
    budget_nrs = [
        nr for nr in result.node_results
        if nr.status == "skipped" and nr.error and "budget_exceeded" in nr.error
    ]
    assert len(success_nrs) == 2, (
        f"expected 2 success, got {len(success_nrs)}; statuses="
        f"{[nr.status for nr in result.node_results]}"
    )
    assert len(budget_nrs) >= 1, (
        f"expected >=1 budget_exceeded skipped, got {len(budget_nrs)}; statuses="
        f"{[(nr.status, nr.error) for nr in result.node_results]}"
    )
    # 部分成功 → overall status='success'(design verify 字面)
    assert result.status == "success"
    # W-P1-2 标记:budget_tripped 显式信号(与 ctx.abort 外部 abort 正交)
    assert result.budget_exceeded is True
    # total_usage = 2 success node × 60 = 120(skipped node 不 spawn 不计)
    assert result.total_usage.input_tokens == 120


# ─────────────────────────────────────────────────────────────────────
# 并发根治断言:N=8 / concurrency=8 / budget=100 / per_call=60 + latency
# → budget_lock 串行根治后 success 恒为 2(第 3 个 check 看到 total=120>100 短路),
# 即使 _FakeAgent.run 内 await asyncio.sleep(0.01) 模拟真实 LLM latency。
# skeptic finding #1 复现脚本(success=8/total=480/budget_bypassed)经此测变红,
# 串行根治后变绿。
# ─────────────────────────────────────────────────────────────────────
def test_budget_concurrency_race_fixed_by_serial_lock():
    agents = [_FakeAgent(per_call_input_tokens=60, latency=0.01) for _ in range(8)]
    cap, restore = _patch_build(agents)
    try:
        e = WorkflowEngine()
        spec = _spec(8)
        result = run_async(e.run(spec, _ctx_with_budget(
            total_tokens_limit=100, concurrency=8,
        )))
    finally:
        restore()
    success_nrs = [nr for nr in result.node_results if nr.status == "success"]
    skipped_nrs = [nr for nr in result.node_results if nr.status == "skipped"]
    # 串行根治:success 恒为 2(第 3 个 sibling 入锁时 total=120>100 短路),其余 skipped
    assert len(success_nrs) == 2, (
        f"budget race not fixed: expected exactly 2 success, got {len(success_nrs)}; "
        f"statuses={[(nr.status, nr.error) for nr in result.node_results]}"
    )
    assert len(skipped_nrs) == 6, (
        f"expected 6 skipped, got {len(skipped_nrs)}; "
        f"statuses={[(nr.status, nr.error) for nr in result.node_results]}"
    )
    # budget 触达,total = 2 × 60 = 120(不是被绕过的 8 × 60 = 480)
    assert result.budget_exceeded is True
    assert result.total_usage.input_tokens == 120, (
        f"budget bypassed: expected total 120, got {result.total_usage.input_tokens}"
    )


# ─────────────────────────────────────────────────────────────────────
# total_usage 累计准确:budget 触达后 skipped node 不计 usage
# ─────────────────────────────────────────────────────────────────────
def test_budget_skipped_nodes_do_not_contribute_usage():
    agents = [_FakeAgent(per_call_input_tokens=60) for _ in range(5)]
    cap, restore = _patch_build(agents)
    try:
        e = WorkflowEngine()
        spec = _spec(5)
        result = run_async(e.run(spec, _ctx_with_budget(total_tokens_limit=100)))
    finally:
        restore()
    # 无论调度:success node 累计 usage = 60 × (success count);其余 skipped
    success_nrs = [nr for nr in result.node_results if nr.status == "success"]
    expected_input = 60 * len(success_nrs)
    assert result.total_usage.input_tokens == expected_input
    # budget 触达(budget_exceeded=True),至少 1 个 skipped
    assert result.budget_exceeded is True
    assert any(nr.status == "skipped" for nr in result.node_results)


# ─────────────────────────────────────────────────────────────────────
# 无 budget_limits 时:行为退化到 P0(全跑完,budget_exceeded=False)
# ─────────────────────────────────────────────────────────────────────
def test_no_budget_limits_runs_all_nodes():
    agents = [_FakeAgent(per_call_input_tokens=60) for _ in range(4)]
    cap, restore = _patch_build(agents)
    try:
        e = WorkflowEngine()
        spec = _spec(4)
        ctx = WorkflowContext(
            session_id="s1", agent_id_prefix="wf", run_id="wf_nobudget",
            concurrency=8,
            # budget_limits=None — 默认值
        )
        result = run_async(e.run(spec, ctx))
    finally:
        restore()
    # 无 budget 罩 → 全部跑完,无短路
    assert result.node_count == 4
    assert all(nr.status == "success" for nr in result.node_results)
    assert result.budget_exceeded is False
    assert result.total_usage.input_tokens == 240  # 4 × 60


# ─────────────────────────────────────────────────────────────────────
# RK3:agent.run 显式传 usage_limits=UsageLimits(request_limit=None)
# (默认 request_limit=50 会在 fan-out N=8+ 提前 UsageLimitExceeded)
# ─────────────────────────────────────────────────────────────────────
def test_spawn_agent_unsets_request_limit_via_usage_limits():
    """RK3:_spawn_agent 调 agent.run(usage_limits=UsageLimits(request_limit=None))。
    设 1 个 node(无 budget 触达),断言 fake agent 收到的 usage_limits.request_limit
    是 None(显式 unset,不是默认 50)。"""
    agents = [_FakeAgent(per_call_input_tokens=10)]
    cap, restore = _patch_build(agents)
    try:
        e = WorkflowEngine()
        spec = _spec(1)
        # 无 budget_limits(budget 罩不参与);只验 RK3 usage_limits 透传
        ctx = WorkflowContext(
            session_id="s1", agent_id_prefix="wf", run_id="wf_rk3",
        )
        run_async(e.run(spec, ctx))
    finally:
        restore()
    assert agents[0].captured_usage_limits is not None
    assert agents[0].captured_usage_limits.request_limit is None, (
        f"RK3: agent.run 必须传 usage_limits=UsageLimits(request_limit=None);"
        f" got request_limit={agents[0].captured_usage_limits.request_limit!r}"
    )


# ─────────────────────────────────────────────────────────────────────
# 首个 node 前 check_before_request(total=0):未超 → 正常 spawn
# (确认短路只发生在已超限,不误伤首 node)
# ─────────────────────────────────────────────────────────────────────
def test_budget_check_before_first_node_passes_when_under_limit():
    agents = [_FakeAgent(per_call_input_tokens=30) for _ in range(2)]
    cap, restore = _patch_build(agents)
    try:
        e = WorkflowEngine()
        spec = _spec(2)
        # budget=100,每 node 30,2 node 累计 60 — 不会触达
        result = run_async(e.run(spec, _ctx_with_budget(total_tokens_limit=100)))
    finally:
        restore()
    assert result.node_count == 2
    assert all(nr.status == "success" for nr in result.node_results)
    assert result.budget_exceeded is False
    assert result.total_usage.input_tokens == 60
