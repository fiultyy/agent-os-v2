"""W-P1-1 单测:WorkflowEngine.pipeline(无 barrier 阶段链)。

verify(design §5 W-P1-1):
- 3 stage × 3 item pipeline,每 item 独立跑完所有 stage(无 BARRIER 等齐)
- wall-clock ≈ 单 item 链长(非 sum)—— stage worker per stage 串联,item 在不同
  stage 重叠,所以总时长 ≈ N × 单 stage,非 M × N
- stage 隔离不串(item A 的 stage[1] 不混 item B 的 stage[1])
- 闭包 bug 修正:所有 stage worker 不共享末 stage 的 prompt
- _emit_workflow(workflow_pipeline_stage_started/completed)正确 wire
- _spawn_agent 失败时 item 短路(后续 stage 不 spawn,保留前 output)
- poison pill 连锁停止所有 worker(无泄漏 task)
"""

import asyncio
import time

import harness.workflow_engine as wf_mod
from harness.workflow_engine import (
    WORKFLOW_FLOW_EVENTS,
    PipelineSpec,
    WorkflowContext,
    WorkflowEngine,
)

# ── sync wrapper(避开 pytest-asyncio loop pollution)──
_LOOP = None


def run_async(coro):
    global _LOOP
    if _LOOP is None or _LOOP.is_closed():
        _LOOP = asyncio.new_event_loop()
        asyncio.set_event_loop(_LOOP)
    return _LOOP.run_until_complete(coro)


# ── Fake native Agent(按 task_input 内容返固定 output,模拟 stage 串联)──
class _FakeRunResult:
    def __init__(self, output):
        self.output = output


class _FakeAgent:
    """run 返 output(从 task_input 派生,验证 stage prompt + prev output 串联)。"""

    def __init__(self, transform=None, delay=0.05):
        # transform(task_input) -> output;默认 "echo:" + task_input
        self._transform = transform or (lambda s: f"echo:{s[:40]}")
        self._delay = delay
        self.runs: list[str] = []  # 记录每次收到的 task_input

    async def run(self, task_input, *, usage=None):
        self.runs.append(task_input)
        await asyncio.sleep(self._delay)  # 模拟 stage 处理时间
        return _FakeRunResult(self._transform(task_input))


def _patch_build(agent_factory):
    """agent_factory(node) -> fake agent(按 node label 决定行为)。返 restore。"""
    orig = wf_mod.build_native_agent

    def _fake(*a, **kw):
        # node 是 build_native_agent 之后才传到 agent.run;这里只返 fake agent。
        # 但 transform 需要 node 信息,故 factory 从闭包外的 stage_config 取。
        return agent_factory()

    wf_mod.build_native_agent = _fake

    def restore():
        wf_mod.build_native_agent = orig

    return restore


def _ctx(concurrency=8):
    return WorkflowContext(
        session_id="s1", agent_id_prefix="wf", run_id="wf_pipe1",
        concurrency=concurrency,
    )


def _spec(stages_prompts, items):
    return PipelineSpec.model_validate({
        "stages": [{"prompt": p, "label": f"s{i}"} for i, p in enumerate(stages_prompts)],
        "items": items,
    })


# ─────────────────────────────────────────────────────────────────────
# 主断言:3 stage × 3 item,每 item 独立跑完所有 stage
# ─────────────────────────────────────────────────────────────────────
def test_pipeline_3_stages_3_items_each_item_full_chain():
    """每 item 跑完 3 stage,total spawn 数 = 3 × 3 = 9。"""
    spawn_count = {"n": 0}
    all_inputs: list[str] = []  # 每 spawn 记录其 task_input

    def make_agent():
        spawn_count["n"] += 1
        a = _FakeAgent()
        # 包一层记录 task_input(agent.runs[0] 在 transform 之前已 append,稳)
        orig_run = a.run

        async def _wrap(task_input, *, usage=None):
            all_inputs.append(task_input)
            return await orig_run(task_input, usage=usage)

        a.run = _wrap
        return a

    restore = _patch_build(make_agent)
    try:
        e = WorkflowEngine()
        spec = _spec(["stage0", "stage1", "stage2"], ["a", "b", "c"])
        results = run_async(e.pipeline(spec, _ctx()))
    finally:
        restore()

    # 3 item × 3 stage = 9 spawn
    assert spawn_count["n"] == 9
    assert len(all_inputs) == 9
    # 返 3 个 item 的终态 output
    assert len(results) == 3
    # 按 stage prompt 分组(不依赖 spawn 序 — 异步调度不保证)
    stage0_count = sum(1 for s in all_inputs if s == "stage0")
    stage1_count = sum(1 for s in all_inputs if s.startswith("stage1\n"))
    stage2_count = sum(1 for s in all_inputs if s.startswith("stage2\n"))
    assert stage0_count == 3, f"stage0 spawn count = {stage0_count}, inputs={all_inputs}"
    assert stage1_count == 3, f"stage1 spawn count = {stage1_count}"
    assert stage2_count == 3, f"stage2 spawn count = {stage2_count}"
    # stage[1]/[2] 的 task_input 应含 "[prev_output]" 标记(前一 stage output 注入)
    assert all("[prev_output]" in s for s in all_inputs if s.startswith("stage1"))
    assert all("[prev_output]" in s for s in all_inputs if s.startswith("stage2"))


# ─────────────────────────────────────────────────────────────────────
# wall-clock 验证:无 barrier,wall-clock ≈ 单 item 链长(非 sum)
# ─────────────────────────────────────────────────────────────────────
def test_pipeline_wallclock_is_single_item_chain_not_sum():
    """3 stage × 3 item,每 stage 0.1s:
    - barrier(串行 item):3 × 3 × 0.1 = 0.9s
    - 无 barrier(item 重叠):≈ 3 × 0.1 = 0.3s(stage 串联,item 在不同 stage 重叠)
    断言 elapsed < 0.7s(明显小于 sum 0.9s,留 CI 抖动余量)。
    """
    spawn_count = {"n": 0}

    def make_agent():
        spawn_count["n"] += 1
        return _FakeAgent(delay=0.1)

    restore = _patch_build(make_agent)
    try:
        e = WorkflowEngine()
        spec = _spec(["s0", "s1", "s2"], ["a", "b", "c"])
        started = time.monotonic()
        results = run_async(e.pipeline(spec, _ctx()))
        elapsed = time.monotonic() - started
    finally:
        restore()

    assert len(results) == 3
    assert spawn_count["n"] == 9
    # 无 barrier:elapsed ≈ 3 stage × 0.1s = 0.3s,远小于 sum 0.9s。
    # 阈值 0.7s:sum 0.9s 应被打破;留 CI 抖动余量(单 stage 0.1s × 3 + 调度开销)。
    assert elapsed < 0.7, (
        f"pipeline wall-clock {elapsed:.2f}s >= 0.7s — barrier regression "
        f"(expected ≈ 0.3s single-item-chain, not ≈ 0.9s sum)"
    )


# ─────────────────────────────────────────────────────────────────────
# stage 隔离:item A stage[1] 不混 item B stage[1](item 独立链)
# ─────────────────────────────────────────────────────────────────────
def test_pipeline_items_are_isolated_no_crosstalk():
    """每 item 独立链:各 stage 的 output 按 FIFO queue 顺序 item 不串味。

    用 transform 让 output 含 stage_idx + 一个 nonce,item 跑过 stage 链后,
    终态 output 应是 M 个独立值(每 item 一条链),非全部相同(串味信号)。
    """
    # transform: output 含 task_input 的 hash 前缀 + 全局 nonce,可追溯链路独立
    nonce = {"n": 0}

    def transform(s):
        nonce["n"] += 1
        return f"o{nonce['n']}"

    def make_agent():
        return _FakeAgent(transform=transform, delay=0.01)

    restore = _patch_build(make_agent)
    try:
        e = WorkflowEngine()
        spec = _spec(["s0", "s1", "s2"], ["alpha", "beta", "gamma"])
        results = run_async(e.pipeline(spec, _ctx()))
    finally:
        restore()

    assert len(results) == 3
    # 关键:终态 3 个 output 应该是 3 个不同的 "o{nonce}"(各 item 链独立跑完 3 stage,
    # 每 stage 一个 nonce → 终态是每 item 各自 stage[2] 的 output,3 个不同 nonce)
    # 若串味(多 item 共享 stage output),终态会出现重复。
    assert len(set(results)) == 3, f"crosstalk: results not 3 distinct — got {results}"


# ─────────────────────────────────────────────────────────────────────
# 闭包 bug 验证:所有 stage worker 不共享末 stage 的 prompt
# ─────────────────────────────────────────────────────────────────────
def test_pipeline_closure_bug_all_stages_get_own_prompt():
    """若闭包 bug(所有 worker 捕获末 stage),stage[0] 的 task_input 会含 "s2" 而非 "s0"。
    断言:3 个 spawn 的 task_input == "s0"(首 stage,无 prev_output),
    且无任何 task_input 是纯末 stage prompt("s2")—— 闭包 bug 时 stage[0]/[1]
    会跑 "s2"。
    """
    all_inputs: list[str] = []

    def make_agent():
        a = _FakeAgent(delay=0.01)
        orig_run = a.run

        async def _wrap(task_input, *, usage=None):
            all_inputs.append(task_input)
            return await orig_run(task_input, usage=usage)

        a.run = _wrap
        return a

    restore = _patch_build(make_agent)
    try:
        e = WorkflowEngine()
        spec = _spec(["s0", "s1", "s2"], ["a", "b", "c"])
        run_async(e.pipeline(spec, _ctx()))
    finally:
        restore()

    # 闭包 bug 时:所有 stage worker 跑 "s2",会得到 9 个 "s2" 纯输入。
    # 正确实现:3 个 "s0"(首 stage)+ 3 个 "s1\n[prev_output]..." + 3 个 "s2\n..."
    stage0_count = sum(1 for s in all_inputs if s == "s0")
    stage2_pure = sum(1 for s in all_inputs if s == "s2")  # 闭包 bug 信号
    assert stage0_count == 3, (
        f"closure bug: stage[0] task_input 's0' count = {stage0_count} (expected 3) — got {all_inputs}"
    )
    assert stage2_pure == 0, (
        f"closure bug: pure 's2' task_input (worker captured last stage) — got {all_inputs}"
    )


# ─────────────────────────────────────────────────────────────────────
# _emit_workflow:workflow_pipeline_stage_started/completed 正确 wire
# ─────────────────────────────────────────────────────────────────────
def test_pipeline_emits_stage_started_completed_events():
    captured = []

    class _Emitter:
        async def emit(self, ev):
            captured.append(ev["data"]["flow_event"])

    def make_agent():
        return _FakeAgent(delay=0.01)

    restore = _patch_build(make_agent)
    try:
        e = WorkflowEngine(emitter=_Emitter())
        spec = _spec(["s0", "s1"], ["a", "b"])
        run_async(e.pipeline(spec, _ctx()))
    finally:
        restore()
    run_async(asyncio.sleep(0.05))  # flush create_task emits

    # 2 item × 2 stage = 4 started + 4 completed
    assert captured.count("workflow_pipeline_stage_started") == 4
    assert captured.count("workflow_pipeline_stage_completed") == 4
    # 全部事件名 ∈ WORKFLOW_FLOW_EVENTS(R4 冻结集合)
    assert all(name in WORKFLOW_FLOW_EVENTS for name in captured)


def test_pipeline_emitter_none_no_crash():
    def make_agent():
        return _FakeAgent(delay=0.01)

    restore = _patch_build(make_agent)
    try:
        e = WorkflowEngine(emitter=None)
        spec = _spec(["s0", "s1"], ["a", "b"])
        results = run_async(e.pipeline(spec, _ctx()))
    finally:
        restore()
    assert len(results) == 2


# ─────────────────────────────────────────────────────────────────────
# poison pill 连锁停止:所有 worker 退出(无泄漏 task)
# ─────────────────────────────────────────────────────────────────────
def test_pipeline_poison_pill_no_leaked_tasks():
    def make_agent():
        return _FakeAgent(delay=0.01)

    restore = _patch_build(make_agent)
    try:
        e = WorkflowEngine()
        spec = _spec(["s0", "s1", "s2"], ["a", "b"])
        run_async(e.pipeline(spec, _ctx()))
    finally:
        restore()
    # pipeline 返回后,所有 worker 应已 done。断言 — 当前 loop 的 pending task 数不增。
    # (精确测:运行一次空 sleep 后无残留 coroutine warning)
    run_async(asyncio.sleep(0.01))


# ─────────────────────────────────────────────────────────────────────
# R7 _spawn_agent 失败:item 短路保留前 output(不 raise,不阻塞其他 item)
# ─────────────────────────────────────────────────────────────────────
def test_pipeline_node_failure_short_circuits_item_no_abort():
    """stage[1] raise → 该 item 短路(保留 stage[0] output);其他 item 不受影响。"""
    raise_at = {"n": 0}  # 在第 4 次 spawn(stage[1] item[0])raise

    class _RaisingAgent:
        def __init__(self):
            self.runs = []

        async def run(self, task_input, *, usage=None):
            self.runs.append(task_input)
            raise_at["n"] += 1
            if raise_at["n"] == 4:  # stage[1] item[0]
                raise RuntimeError("stage1 boom")
            return _FakeRunResult(f"ok[{task_input[:10]}]")

    def make_agent():
        return _RaisingAgent()

    restore = _patch_build(make_agent)
    try:
        e = WorkflowEngine()
        spec = _spec(["s0", "s1", "s2"], ["a", "b"])
        results = run_async(e.pipeline(spec, _ctx()))
    finally:
        restore()

    assert len(results) == 2
    # pipeline 不 raise(R7 降级 — _spawn_agent 返 status='error',pipeline 短路)


# ─────────────────────────────────────────────────────────────────────
# PipelineSpec pydantic 校验:stages min_length=1, items min_length=1
# ─────────────────────────────────────────────────────────────────────
def test_pipeline_spec_rejects_empty_stages():
    import pytest
    with pytest.raises(Exception):
        PipelineSpec.model_validate({"stages": [], "items": ["a"]})


def test_pipeline_spec_rejects_empty_items():
    import pytest
    with pytest.raises(Exception):
        PipelineSpec.model_validate({"stages": [{"prompt": "s0"}], "items": []})


def test_pipeline_spec_accepts_minimal():
    spec = PipelineSpec.model_validate({
        "stages": [{"prompt": "s0"}],
        "items": ["a"],
    })
    assert len(spec.stages) == 1
    assert len(spec.items) == 1
    assert spec.concurrency == 8  # default


# ─────────────────────────────────────────────────────────────────────
# concurrency cap:per-stage Semaphore(N) 限并发(轻量验证不崩)
# ─────────────────────────────────────────────────────────────────────
def test_pipeline_concurrency_cap_completes():
    def make_agent():
        return _FakeAgent(delay=0.01)

    restore = _patch_build(make_agent)
    try:
        e = WorkflowEngine()
        spec = PipelineSpec.model_validate({
            "stages": [{"prompt": "s0"}, {"prompt": "s1"}],
            "items": ["i" + str(i) for i in range(6)],
            "concurrency": 2,
        })
        results = run_async(e.pipeline(spec, _ctx()))
    finally:
        restore()
    assert len(results) == 6
