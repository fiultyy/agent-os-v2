"""W-P2-2 单测:workflow_engine.nesting(一层嵌套限制 + ContextVar 栈式回退)。

verify(design §5 W-P2-2):
- (a) root → child 正常:nested_run 在 root(depth=0)上下文调 engine.run 完成。
- (b) child 内再 nested_run raise WorkflowNestingError:depth>=1 时 one-level limit。
- (c) 并发 2 nested_run(asyncio.gather)child_ctx 不串味:两 child 各自独立
  run_id / depth,共享父 total_usage 引用(ContextVar task-local 不互染)。
- (d) 父 abort.set() 后 child _spawn_agent 短路:共享 Event 引用,child 的 run._bounded
  入口见 abort 即返 skipped。
"""

import asyncio

import harness.workflow_engine as wf_mod
from harness.workflow_engine import (
    WorkflowContext,
    WorkflowEngine,
    WorkflowNestingError,
    WorkflowNodesSpec,
    nested_run,
    set_engine,
)
from pydantic_ai.usage import RunUsage

# ── sync wrapper(避开 pytest-asyncio loop pollution,见
#    feedback-pytest-asyncio-loop-pollution + run 测试母版)──
_LOOP = None


def run_async(coro):
    global _LOOP
    if _LOOP is None or _LOOP.is_closed():
        _LOOP = asyncio.new_event_loop()
        asyncio.set_event_loop(_LOOP)
    return _LOOP.run_until_complete(coro)


# ── Fake native Agent(同 test_workflow_engine_run.py 母版)────────────
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
            usage.requests = (usage.requests or 0) + 1
            usage.input_tokens = (usage.input_tokens or 0) + 10
            usage.output_tokens = (usage.output_tokens or 0) + 5
            self.run_usage = usage
        if self._exc is not None:
            raise self._exc
        return _FakeRunResult(self._output)


# ── monkeypatch helper(同 run 测试母版;经 wf_mod.build_native_agent 替换)──
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


def _spec(nodes_dicts, fan_in="list"):
    return WorkflowNodesSpec.model_validate({"nodes": nodes_dicts, "fan_in": fan_in})


def _root_ctx(concurrency=8, **kw):
    """root ctx:depth=0(顶层)。"""
    return WorkflowContext(
        session_id="s1", agent_id_prefix="wf", run_id="wf_root",
        concurrency=concurrency, **kw,
    )


def _setup_engine():
    """set_engine(WorkflowEngine())注入 — nesting._get_engine 返此实例。

    返 (engine, restore)。engine.emitter=None(_emit_workflow 静默跳过)。
    """
    engine = WorkflowEngine()
    set_engine(engine)

    def restore():
        # 重置 nesting 模块级单例为 None(下次 _get_engine 重建)
        from harness.workflow_engine import nesting as nest_mod
        nest_mod._engine_instance = None

    return engine, restore


# ─────────────────────────────────────────────────────────────────────
# (a) root → child 正常:nested_run 完成且 child_ctx.depth=1
# ─────────────────────────────────────────────────────────────────────
def test_nested_run_root_to_child_success():
    agents = [_FakeAgent(output="child_a"), _FakeAgent(output="child_b")]
    cap, restore_build = _patch_build(agents)
    _, restore_engine = _setup_engine()
    try:
        parent_ctx = _root_ctx()
        spec = _spec([{"prompt": "t1"}, {"prompt": "t2"}])
        result = run_async(nested_run(spec, parent_ctx))
    finally:
        restore_build()
        restore_engine()

    # 两子 agent 各 spawn 一次
    assert cap["count"] == 2
    assert result.node_count == 2
    assert all(nr.status == "success" for nr in result.node_results)
    # child run_id 是新生成(wf_<hex12>),非 parent 的 wf_root
    assert result.run_id != "wf_root"
    assert result.run_id.startswith("wf_")


def test_nested_run_child_depth_is_one():
    """nested_run 调后,_WF_CTX 已 reset 回 None(栈式 finally 回退)。"""
    agents = [_FakeAgent(output="ok")]
    _, restore_build = _patch_build(agents)
    _, restore_engine = _setup_engine()
    try:
        parent_ctx = _root_ctx()
        run_async(nested_run(_spec([{"prompt": "t"}]), parent_ctx))
    finally:
        restore_build()
        restore_engine()
    # 栈式回退:_WF_CTX 重置回调用前状态(root 调用前是 None)
    from harness.workflow_engine import get_workflow_context
    assert get_workflow_context() is None


# ─────────────────────────────────────────────────────────────────────
# (b) child 内再 nested_run raise WorkflowNestingError(one-level limit)
# ─────────────────────────────────────────────────────────────────────
def test_nested_run_child_cannot_nest_again():
    """在已是 child 的 _WF_CTX 内再调 nested_run → raise WorkflowNestingError。"""
    agents = [_FakeAgent(output="ok")]
    _, restore_build = _patch_build(agents)
    _, restore_engine = _setup_engine()
    try:
        parent_ctx = _root_ctx()
        # 顶层 nested_run 模拟"child 调 nested_run":手动先 set child_ctx(depth=1)
        from harness.workflow_engine import _WF_CTX
        child_already = WorkflowContext(
            session_id="s1", agent_id_prefix="wf", run_id="wf_child_already",
            depth=1,
        )
        token = _WF_CTX.set(child_already)
        try:
            raised = False
            try:
                run_async(nested_run(_spec([{"prompt": "t"}]), parent_ctx))
            except WorkflowNestingError:
                raised = True
            assert raised, "child 内再 nested_run 应 raise WorkflowNestingError"
        finally:
            _WF_CTX.reset(token)
    finally:
        restore_build()
        restore_engine()


# ─────────────────────────────────────────────────────────────────────
# (c) 并发 2 nested_run(asyncio.gather)child_ctx 不串味
# 两 child 各自独立 run_id / depth=1,共享父 total_usage 引用(父 token 累加)。
# ContextVar task-local:sibling task 的 set/reset 不互染。
# ─────────────────────────────────────────────────────────────────────
def test_concurrent_nested_runs_do_not_cross_contaminate():
    """asyncio.gather 并发 2 nested_run,两 child 的 run_id 独立 + 不串味。

    共享父 total_usage:两 child 各 spawn 1 agent(+1 request each)→ 父
    total_usage.requests == 2(同一 RunUsage 引用累加)。
    """
    agents = [_FakeAgent(output="c1"), _FakeAgent(output="c2")]
    cap, restore_build = _patch_build(agents)
    _, restore_engine = _setup_engine()
    try:
        parent_ctx = _root_ctx()
        spec = _spec([{"prompt": "t"}])

        async def _two_concurrent():
            r1, r2 = await asyncio.gather(
                nested_run(spec, parent_ctx),
                nested_run(spec, parent_ctx),
            )
            return r1, r2

        r1, r2 = run_async(_two_concurrent())
    finally:
        restore_build()
        restore_engine()

    # 两 child 各 spawn 1 次(共 2 次)
    assert cap["count"] == 2
    # 两 child run_id 独立 + 不同
    assert r1.run_id != r2.run_id
    assert r1.run_id.startswith("wf_") and r2.run_id.startswith("wf_")
    # 各 1 个 success node
    assert r1.node_count == 1 and r2.node_count == 1
    assert r1.node_results[0].output == "c1"
    assert r2.node_results[0].output == "c2"
    # 共享父 total_usage:两 child 各 +1 request → 父 ctx.total_usage.requests == 2
    # (parent_ctx.total_usage 是同一 RunUsage 引用,两 child fan-in commit 累加)
    assert parent_ctx.total_usage.requests == 2


# ─────────────────────────────────────────────────────────────────────
# (d) 父 abort.set() 后 child _spawn_agent 短路
# child 经 run._bounded 入口的 ctx.abort.is_set() 检查(engine.py:417-423)
# 返 skipped status(不 spawn agent)。
# ─────────────────────────────────────────────────────────────────────
def test_parent_abort_short_circuits_child_spawn():
    """父 abort.set() 后 child 的 run._bounded 见 abort → skipped,不 spawn。"""
    agents = [_FakeAgent(output="should_not_run")]  # 不应被消费
    cap, restore_build = _patch_build(agents)
    _, restore_engine = _setup_engine()
    try:
        parent_ctx = _root_ctx()
        # 父 ctx 设 abort Event 并 set()(模拟父已 abort)
        parent_ctx.abort = asyncio.Event()
        parent_ctx.abort.set()
        spec = _spec([{"prompt": "t1"}, {"prompt": "t2"}])
        result = run_async(nested_run(spec, parent_ctx))
    finally:
        restore_build()
        restore_engine()

    # abort 短路:_spawn_agent 不被调(build_native_agent 计数=0)
    assert cap["count"] == 0
    # 两 node 全 skipped(abort 广播,run._bounded 入口返 skipped)
    assert result.node_count == 2
    assert all(nr.status == "skipped" for nr in result.node_results)
    # overall status='error'(无 success node)
    assert result.status == "error"


# ─────────────────────────────────────────────────────────────────────
# 共享引用断言:child 运行后,parent_ctx.total_usage 经 mutable incr 含 child 用量
# (design §7 caveat — 父子 token 汇入同一 budget;engine.run 内部 fan-in ``+=``
# 重绑新对象,故 nesting 层在 finally 经 ``RunUsage.incr`` 回 commit)
# ─────────────────────────────────────────────────────────────────────
def test_child_commits_usage_back_to_parent_via_incr():
    """nested_run 后 parent_ctx.total_usage 含 child 累计用量(回 commit)。

    child spawn 1 agent(+1 request / +10 input / +5 output)→ parent_ctx.total_usage
    经 ``RunUsage.incr`` 原地加(design §7 caveat:父子 token 汇入同一 budget)。
    """
    agents = [_FakeAgent(output="ok")]
    _, restore_build = _patch_build(agents)
    _, restore_engine = _setup_engine()
    try:
        parent_ctx = _root_ctx()
        # 记 parent 初始 total_usage 引用(回 commit 应原地 mutate 此对象)
        parent_usage_obj = parent_ctx.total_usage
        run_async(nested_run(_spec([{"prompt": "t"}]), parent_ctx))
    finally:
        restore_build()
        restore_engine()

    # parent_ctx.total_usage 引用未变(nesting 经 incr 原地 mutate,不重绑)
    assert parent_ctx.total_usage is parent_usage_obj
    # 含 child 的 1 request / 10 input / 5 output
    assert parent_ctx.total_usage.requests == 1
    assert parent_ctx.total_usage.input_tokens == 10
    assert parent_ctx.total_usage.output_tokens == 5
