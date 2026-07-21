"""W-P1-3 单测:WorkflowEngine.loop — while + seen 去重 + dry counter + budget guard。

verify(design §5 W-P1-3 字面):
- mock finder 返重复候选 → dry_counter 累加到 ``dry_limit=2`` break
- mock finder 返新候选 → ``dry_counter=0`` 重置(修正 design [2] 不重置 bug)
- budget.total_tokens_limit 超限 → 每轮前 check_before_request 短路 break

红线:R1/R2/R5 经 _spawn_agent 零增量负担(loop 仅复用 _spawn_agent,不另起
spawn 路径);R3 fire-and-forget finder error 透传为 NodeResult(status='error')
不冒泡 loop;R4 loop_started/iteration/completed 事件 wire tick_completed
(``data.flow_event ∈ WORKFLOW_FLOW_EVENTS``)。
"""

import asyncio

import harness.workflow_engine as wf_mod
from harness.workflow_engine import (
    LoopSpec,
    WorkflowContext,
    WorkflowEngine,
    WorkflowNodeSpec,
    _extract_candidates,
    _seen_key,
)
from pydantic_ai.usage import RunUsage, UsageLimits

# ── sync wrapper(避 pytest-asyncio loop pollution,见 budget 测试母版)──
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
    """``outputs`` 是每次 run 返回的 output 序列(支持脚本化重复/新候选轮替)。"""

    def __init__(self, outputs, per_call_input_tokens=10):
        self._outputs = list(outputs)
        self._per_call_input = per_call_input_tokens
        self.captured_usage_limits = None

    async def run(self, task_input, *, usage=None, usage_limits=None):
        self.captured_usage_limits = usage_limits
        if usage is not None:
            usage.requests = (usage.requests or 0) + 1
            usage.input_tokens = (usage.input_tokens or 0) + self._per_call_input
        if not self._outputs:
            return _FakeRunResult([])
        return _FakeRunResult(self._outputs.pop(0))


def _patch_build(agents):
    """patch build_native_agent → 返 agents 序列;耗尽后返最后一个 agent
    (避免 StopIteration 在协程里被转 RuntimeError 干扰断言)。"""
    orig = wf_mod.build_native_agent
    _idx = {"i": 0}
    _agents = list(agents)

    def _fake(*a, **kw):
        if _idx["i"] < len(_agents):
            ag = _agents[_idx["i"]]
            _idx["i"] += 1
            return ag
        return _agents[-1]  # 耗尽复用末 agent(loop 测试期望调用次数 ≤ len)

    wf_mod.build_native_agent = _fake

    def restore():
        wf_mod.build_native_agent = orig

    return restore


def _ctx(run_id="wf_loop1", budget_limits=None):
    return WorkflowContext(
        session_id="s1",
        agent_id_prefix="wf",
        run_id=run_id,
        concurrency=8,
        budget_limits=budget_limits,
    )


def _finder_spec():
    return LoopSpec.model_validate({"finder_spec": {"prompt": "find", "label": "f1"}})


# ─────────────────────────────────────────────────────────────────────
# 基本断言 1:重复候选 → dry_counter 累加到 dry_limit=2 break
# (第 1 轮入 seen,第 2-3 轮全旧 → dry=2 break;总轮数 = 3)
# ─────────────────────────────────────────────────────────────────────
def test_dry_counter_breaks_on_repeated_candidates():
    # finder 连续 3 轮返同一候选:["cand_A"] x3
    agent = _FakeAgent([["cand_A"], ["cand_A"], ["cand_A"]])
    restore = _patch_build([agent])
    try:
        e = WorkflowEngine()
        spec = _finder_spec()
        ctx = _ctx()
        result = run_async(e.loop(spec, ctx))
    finally:
        restore()

    # 第 1 轮:cand_A 新,dry=0;第 2 轮:旧,dry=1;第 3 轮:旧,dry=2 → break
    assert result.node_count == 3, (
        f"expected 3 iters (dry_limit=2 after 2 dry rounds), got {result.node_count}"
    )
    # 最终 dry_counter = 2(触达 limit)
    assert ctx.dry_counter == 2, (
        f"expected dry_counter=2 at break, got {ctx.dry_counter}"
    )
    # seen 只含 cand_A 的 content_hash(去重生效)
    assert len(ctx.seen) == 1, f"expected 1 unique candidate in seen, got {len(ctx.seen)}"
    assert result.status == "success"


# ─────────────────────────────────────────────────────────────────────
# 基本断言 2:新候选到达 → dry=0 重置(修正 design [2] 不重置 bug)
# 轮序:[A] → [A](dry=1) → [B](新,dry=0 重置) → [B](dry=1) → [B](dry=2 break)
# ─────────────────────────────────────────────────────────────────────
def test_new_candidate_resets_dry_counter():
    agent = _FakeAgent([
        ["A"], ["A"], ["B"], ["B"], ["B"],
    ])
    restore = _patch_build([agent])
    try:
        e = WorkflowEngine()
        spec = _finder_spec()
        ctx = _ctx()
        result = run_async(e.loop(spec, ctx))
    finally:
        restore()

    # 5 轮全跑完(dry 在第 5 轮触达 2 break)
    assert result.node_count == 5, (
        f"expected 5 iters (dry reset by new cand B at iter 2), got {result.node_count}"
    )
    # 最终 dry_counter = 2(第 4/5 轮全 B 触达)
    assert ctx.dry_counter == 2, f"final dry_counter={ctx.dry_counter}"
    # seen 含 A + B 两个
    assert len(ctx.seen) == 2, f"expected 2 unique candidates (A,B), got {len(ctx.seen)}"
    assert result.status == "success"


# ─────────────────────────────────────────────────────────────────────
# 断言 3:dry_limit 边界 — dry_limit=1 时第 2 轮重复即 break(2 轮)
# ─────────────────────────────────────────────────────────────────────
def test_dry_limit_1_breaks_after_first_dry_round():
    agent = _FakeAgent([["A"], ["A"], ["A"]])
    restore = _patch_build([agent])
    try:
        e = WorkflowEngine()
        spec = LoopSpec.model_validate({
            "finder_spec": {"prompt": "find", "label": "f1"},
            "dry_limit": 1,
        })
        ctx = _ctx()
        result = run_async(e.loop(spec, ctx))
    finally:
        restore()

    # 第 1 轮 A 新(dry=0);第 2 轮 A 旧(dry=1 ≥ 1 break)
    assert result.node_count == 2, (
        f"expected 2 iters (dry_limit=1 after 1 dry round), got {result.node_count}"
    )
    assert ctx.dry_counter == 1


# ─────────────────────────────────────────────────────────────────────
# 断言 4:max_iter 上限 — 持续新候选不收敛,到 max_iter break
# ─────────────────────────────────────────────────────────────────────
def test_max_iter_cap_breaks_when_always_new_candidates():
    # 每轮一个全新候选 → dry 永远 0,到 max_iter break
    outputs = [[f"cand_{i}"] for i in range(20)]
    agent = _FakeAgent(outputs)
    restore = _patch_build([agent])
    try:
        e = WorkflowEngine()
        spec = LoopSpec.model_validate({
            "finder_spec": {"prompt": "find", "label": "f1"},
            "max_iter": 5,
        })
        ctx = _ctx()
        result = run_async(e.loop(spec, ctx))
    finally:
        restore()

    assert result.node_count == 5, (
        f"expected max_iter=5 cap, got {result.node_count}"
    )
    assert ctx.dry_counter == 0
    assert len(ctx.seen) == 5
    assert result.status == "success"


# ─────────────────────────────────────────────────────────────────────
# 断言 5:budget.total_tokens_limit 超限 → 每轮前 check_before_request 短路 break
# (3 轮 × 10 token,budget=15 → 第 2 轮前 check(10<15)过,第 3 轮前 check
#  (20>15)超限 break;实际 finder 跑 2 轮)
# ─────────────────────────────────────────────────────────────────────
def test_budget_short_circuits_loop():
    agent = _FakeAgent(
        [["A"], ["B"], ["C"], ["D"]],  # 第 3 轮不会被跑(check_before_request 先 break)
        per_call_input_tokens=10,
    )
    restore = _patch_build([agent])
    try:
        e = WorkflowEngine()
        spec = LoopSpec.model_validate({
            "finder_spec": {"prompt": "find", "label": "f1"},
            "max_iter": 10,
            "budget": {"total_tokens_limit": 15},
        })
        ctx = _ctx(budget_limits=None)  # spec.budget 同步到 ctx
        result = run_async(e.loop(spec, ctx))
    finally:
        restore()

    # 前 2 轮跑完(累计 20);第 3 轮前 check(20>15)超限 break — finder 跑 2 轮
    assert result.node_count == 2, (
        f"expected 2 iters before budget break (20>15 at iter 3 pre-check), "
        f"got {result.node_count}"
    )
    assert result.budget_exceeded is True
    assert ctx.budget_tripped is True
    # total_usage = 2 轮 × 10 = 20(finder 跑过的)
    assert result.total_usage.input_tokens == 20


# ─────────────────────────────────────────────────────────────────────
# 断言 6:finder error 透传不 abort loop(error 轮 status='error',整体仍 'error')
# (R3 fire-and-forget:error 不冒泡,但 overall status 反映有 error)
# ─────────────────────────────────────────────────────────────────────
def test_finder_error_propagated_as_error_node_no_raise():
    class _ExplodingAgent:
        async def run(self, task_input, *, usage=None, usage_limits=None):
            raise RuntimeError("finder boom")

    restore = _patch_build([_ExplodingAgent()])
    try:
        e = WorkflowEngine()
        spec = _finder_spec()
        ctx = _ctx()
        result = run_async(e.loop(spec, ctx))
    finally:
        restore()

    # finder raise → _spawn_agent 降级 NodeResult(status='error');output=None
    # → _extract_candidates 返 [] → dry_counter 每轮 +1 → 第 2 轮(dry>=2)break
    assert all(nr.status == "error" for nr in result.node_results)
    assert result.node_count <= 3  # dry_limit=2:前 2 轮 raise,第 2 轮 dry=2 break
    # overall 'error'(无 success node)
    assert result.status == "error"


# ─────────────────────────────────────────────────────────────────────
# 断言 7:seen_key_fn='label' 用字面而非 content_hash
# ─────────────────────────────────────────────────────────────────────
def test_seen_key_fn_label_uses_literal_string():
    agent = _FakeAgent([["A"], ["A"], ["A"]])
    restore = _patch_build([agent])
    try:
        e = WorkflowEngine()
        spec = LoopSpec.model_validate({
            "finder_spec": {"prompt": "find", "label": "f1"},
            "seen_key_fn": "label",
        })
        ctx = _ctx()
        result = run_async(e.loop(spec, ctx))
    finally:
        restore()

    # 'label' 模式 → seen 直含字面 "A"(非 sha256)
    assert "A" in ctx.seen, f"expected literal 'A' in seen (seen_key_fn=label), got {ctx.seen}"
    assert len(ctx.seen) == 1


# ─────────────────────────────────────────────────────────────────────
# 断言 8:_extract_candidates / _seen_key 单元行为(直接模块函数)
# ─────────────────────────────────────────────────────────────────────
def test_extract_candidates_normalizes_output_shapes():
    assert _extract_candidates(None) == []
    assert _extract_candidates("solo") == ["solo"]
    assert _extract_candidates(["a", "b"]) == ["a", "b"]
    assert _extract_candidates((1, 2)) == ["1", "2"]
    assert _extract_candidates({"x", "y"})  # set → list[str] (顺序无关)


def test_seen_key_content_hash_is_sha256_prefix():
    k1 = _seen_key("cand", "content_hash")
    k2 = _seen_key("cand", "content_hash")
    assert k1 == k2  # deterministic
    assert len(k1) == 16
    assert k1 != "cand"  # not literal
    # content_hash vs label 区分
    assert _seen_key("cand", "label") == "cand"
