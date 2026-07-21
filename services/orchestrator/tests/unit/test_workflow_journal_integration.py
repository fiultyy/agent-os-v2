"""W-P2-4 单测:_emit_workflow / _spawn_agent journal hook 同步双写集成。

verify(design §5 W-P2-4):
- run 后 journal 收齐 started + result 事件(每 node 一对)。
- run 末态 mark_completed(workflow_run.status='completed')。
- e2e mock 中断后 resume(run_id) 完成:已完成 agent 走 cache 不重跑
  (fresh build_native_agent 调用次数 == 待重跑 node 数,不含已 cache node)。

复用 test_workflow_journal.py 的 _RoutingFakeAgent / run_async 母版,保持一致。
"""

import asyncio
import json
import os
import tempfile

import pytest
from pydantic_ai.usage import RunUsage

import harness.workflow_engine as wf_mod
from harness.workflow_engine import (
    WorkflowContext,
    WorkflowEngine,
    WorkflowNodesSpec,
)
from harness.workflow_engine.journal import WorkflowJournal, _cache_key

# ── sync wrapper(避开 pytest-asyncio loop pollution)──────────────────
_LOOP = None


def run_async(coro):
    global _LOOP
    if _LOOP is None or _LOOP.is_closed():
        _LOOP = asyncio.new_event_loop()
        asyncio.set_event_loop(_LOOP)
    return _LOOP.run_until_complete(coro)


# ── Fake native Agent(同 test_workflow_journal.py 母版)────────────────
class _FakeRunResult:
    def __init__(self, output):
        self.output = output


class _RoutingFakeAgent:
    """按 task_input 路由到 output(prompt → out);记录 run 调用次数。"""

    def __init__(self, outputs_by_prompt):
        self._outputs = outputs_by_prompt
        self.run_count = 0

    async def run(self, task_input, *, usage=None, usage_limits=None):
        self.run_count += 1
        for prompt, out in self._outputs.items():
            if prompt in task_input:
                if usage is not None:
                    usage.incr(RunUsage(input_tokens=5, output_tokens=3, requests=1))
                return _FakeRunResult(out)
        if usage is not None:
            usage.incr(RunUsage(input_tokens=1, requests=1))
        return _FakeRunResult("fallback")


class _CountingFactory:
    """返 _RoutingFakeAgent,累计 build_native_agent 调用次数(用于 resume 断言
    fresh 重跑数)。"""

    def __init__(self, outputs_by_prompt):
        self._outputs = outputs_by_prompt
        self.build_count = 0

    def __call__(self, **kwargs):
        self.build_count += 1
        return _RoutingFakeAgent(self._outputs)


@pytest.fixture
def patched_engine(monkeypatch):
    """patch package-level build_native_agent(wf_mod = harness.workflow_engine)。

    engine.py 的 ``_resolve_build_native_agent()`` 经包 namespace 取最新值
    (见 engine.py:32-42 docstring),故 patch 目标必须是包属性而非子模块属性。
    """
    engine = WorkflowEngine(emitter=None, pitfail_registry=None, tool_executor=None)
    return engine, wf_mod, monkeypatch


def _new_journal(tmp_path):
    db = str(tmp_path / "wf.db")
    if os.path.exists(db):
        os.unlink(db)
    return WorkflowJournal(db)


def _make_ctx(journal, run_id="wf_int_1"):
    return WorkflowContext(
        session_id="sess_int",
        agent_id_prefix="wf",
        run_id=run_id,
        concurrency=4,
        journal=journal,
    )


# ─────────────────────────────────────────────────────────────────────
# verify 1:run 后 journal 收齐 started + result 事件
# ─────────────────────────────────────────────────────────────────────
def test_run_writes_started_and_result_events(patched_engine, tmp_path):
    """2-node fan-out → journal 收 2 started + 2 result,workflow_run.status=completed。"""
    engine, wf_mod_pkg, monkeypatch = patched_engine
    j = _new_journal(tmp_path)
    factory = _CountingFactory({"prompt_a": "out_a", "prompt_b": "out_b"})
    monkeypatch.setattr(wf_mod_pkg, "build_native_agent", factory)

    spec = WorkflowNodesSpec(nodes=[
        {"prompt": "prompt_a", "label": "n1"},
        {"prompt": "prompt_b", "label": "n2"},
    ])
    ctx = _make_ctx(j, "wf_int_full")

    res = run_async(engine.run(spec, ctx))
    assert res.status == "success"
    assert len(res.node_results) == 2

    events = j.list_events("wf_int_full")
    started = [e for e in events if e.type == "started"]
    results = [e for e in events if e.type == "result"]
    assert len(started) == 2, f"expected 2 started, got {len(started)}"
    assert len(results) == 2, f"expected 2 result, got {len(results)}"

    # 每 node 的 started/result 共享同一 cache key(prompt+opts 派生)
    started_keys = {e.key for e in started}
    result_keys = {e.key for e in results}
    assert started_keys == result_keys, "started/result pair must share cache key"
    assert started_keys == {
        _cache_key("prompt_a", {"model": None}),
        _cache_key("prompt_b", {"model": None}),
    }

    # result payload 含 status / output
    for ev in results:
        assert ev.payload["status"] == "success"
        assert "output" in ev.payload

    # run 终态写回
    row = j.fetch_run("wf_int_full")
    assert row["status"] == "completed"
    assert row["finished_at"] is not None
    # spec_json 已落盘(resume 重建所需)
    spec_loaded = json.loads(row["spec_json"])
    assert len(spec_loaded["nodes"]) == 2
    j.close()


def test_run_journal_none_is_noop(patched_engine, tmp_path):
    """ctx.journal=None(P2 未通电场景)→ run 不崩,无任何落盘。"""
    engine, wf_mod_pkg, monkeypatch = patched_engine
    factory = _CountingFactory({"p1": "ok"})
    monkeypatch.setattr(wf_mod_pkg, "build_native_agent", factory)

    spec = WorkflowNodesSpec(nodes=[{"prompt": "p1", "label": "n1"}])
    ctx = WorkflowContext(
        session_id="s", agent_id_prefix="wf", run_id="wf_no_journal",
        journal=None,  # 关键:不落盘
    )
    res = run_async(engine.run(spec, ctx))
    assert res.status == "success"
    assert factory.build_count == 1  # 确实跑了


def test_spawn_error_writes_result_with_error_status(patched_engine, tmp_path):
    """agent.run raise → _spawn_agent 仍写 result(type='result', status='error'),
    不漏写(journal 完整性 — resume 据此判断 node 未半完成,cache 仍命中)。"""
    engine, wf_mod_pkg, monkeypatch = patched_engine

    class _RaisingAgent:
        async def run(self, task_input, *, usage=None, usage_limits=None):
            raise RuntimeError("boom")

    monkeypatch.setattr(wf_mod_pkg, "build_native_agent", lambda **kw: _RaisingAgent())

    j = _new_journal(tmp_path)
    spec = WorkflowNodesSpec(nodes=[{"prompt": "p_err", "label": "n_err"}])
    ctx = _make_ctx(j, "wf_int_err")

    res = run_async(engine.run(spec, ctx))
    assert res.status == "error"

    events = j.list_events("wf_int_err")
    results = [e for e in events if e.type == "result"]
    assert len(results) == 1
    assert results[0].payload["status"] == "error"
    assert "boom" in (results[0].payload.get("error") or "")
    j.close()


# ─────────────────────────────────────────────────────────────────────
# verify 2:中断后 resume — 已完成 agent 走 cache 不重跑
# ─────────────────────────────────────────────────────────────────────
def test_resume_after_interrupt_skips_cached_agents(patched_engine, tmp_path):
    """e2e mock 中断场景:

    Step 1: 跑 2-node workflow 完整 → 2 started + 2 result, status=completed。
    Step 2: 模拟中断 — 手动把 status 改回 'running' + 删 n2 的 result 事件
            (模拟 n2 半完成:只有 started 无 result)。
    Step 3: resume(run_id) → n1 cache(不重跑), n2 重跑(factory.build_count == 1)。
    """
    engine, wf_mod_pkg, monkeypatch = patched_engine
    j = _new_journal(tmp_path)

    # Step 1: 完整跑一遍(2 node 都 success)
    factory1 = _CountingFactory({"p_one": "out_one", "p_two": "out_two"})
    monkeypatch.setattr(wf_mod_pkg, "build_native_agent", factory1)
    spec = WorkflowNodesSpec(nodes=[
        {"prompt": "p_one", "label": "n1"},
        {"prompt": "p_two", "label": "n2"},
    ])
    ctx = _make_ctx(j, "wf_int_resume")
    res = run_async(engine.run(spec, ctx))
    assert res.status == "success"

    # Step 2: 模拟中断 — 把 run status 改回 'running' + 删 n2 的 result 事件
    #   (n2 保留 started → 半完成场景)
    j._conn.execute(
        "UPDATE workflow_run SET status='running', finished_at=NULL WHERE run_id=?",
        ("wf_int_resume",),
    )
    j._conn.execute(
        """DELETE FROM workflow_event
           WHERE run_id=? AND type='result' AND node_label='n2'""",
        ("wf_int_resume",),
    )
    j._conn.commit()
    # sanity:n2 现在只有 started(n1 完整)
    events_after = j.list_events("wf_int_resume")
    n2_results = [e for e in events_after if e.node_label == "n2" and e.type == "result"]
    assert len(n2_results) == 0, "fixture setup: n2 result deleted"
    n1_results = [e for e in events_after if e.node_label == "n1" and e.type == "result"]
    assert len(n1_results) == 1, "fixture setup: n1 result kept"

    # Step 3: resume — 用新 factory(fresh 计数器),n2 重跑一次,n1 走 cache(0 次)
    factory2 = _CountingFactory({"p_two": "out_two_v2"})
    monkeypatch.setattr(wf_mod_pkg, "build_native_agent", factory2)
    outcome = run_async(j.resume("wf_int_resume", engine))

    assert outcome["status"] == "resumed"
    assert outcome["cached"] == 1, "n1 cache hit"
    assert outcome["fresh"] == 1, "n2 rerun"
    # 关键断言:n2 被重跑(build_count == 1),n1 不重跑(不进 build count)
    assert factory2.build_count == 1, (
        f"cached agent must not be re-spawned; got build_count={factory2.build_count}"
    )

    # resume 终态
    row = j.fetch_run("wf_int_resume")
    assert row["status"] == "completed"
    j.close()


def test_resume_completed_run_no_fresh_builds(patched_engine, tmp_path):
    """run 已 completed(resume_skip 路径)→ engine.run 完全不调,fresh build=0。"""
    engine, wf_mod_pkg, monkeypatch = patched_engine
    j = _new_journal(tmp_path)
    factory = _CountingFactory({"p_x": "ok"})
    monkeypatch.setattr(wf_mod_pkg, "build_native_agent", factory)

    spec = WorkflowNodesSpec(nodes=[{"prompt": "p_x", "label": "nx"}])
    ctx = _make_ctx(j, "wf_int_skip")
    run_async(engine.run(spec, ctx))
    assert factory.build_count == 1

    # resume 同 run_id → replay_skip,不重跑
    outcome = run_async(j.resume("wf_int_skip", engine))
    assert outcome["status"] == "replay_skip"
    assert factory.build_count == 1, "completed run must not be re-run"
    j.close()


# ─────────────────────────────────────────────────────────────────────
# verify 3:journal 双写幂等 — started 在 resume 重跑时 IntegrityError 吞掉
# ─────────────────────────────────────────────────────────────────────
def test_resume_rerun_partial_does_not_raise(patched_engine, tmp_path):
    """半完成 node(只有 started)resume 重跑 → _spawn_agent 再次写 started 触发
    UNIQUE INDEX 冲突 → _journal_append 吞 IntegrityError 不 abort(resume 完成)。

    这是 W-P2-4 关键集成行为:journal hook 在 resume 重跑场景必须幂等。
    """
    engine, wf_mod_pkg, monkeypatch = patched_engine
    j = _new_journal(tmp_path)

    # 手动 seed:run 'running' + n1 完成 + n2 仅 started
    run_id = "wf_int_idem"
    j.start_run(run_id, "{}", "sess_int")
    nodes = [
        {"prompt": "q1", "label": "n1"},
        {"prompt": "q2", "label": "n2"},
    ]
    j._conn.execute(
        "UPDATE workflow_run SET spec_json=? WHERE run_id=?",
        (json.dumps({"nodes": nodes, "fan_in": "list", "timeout_per_node_ms": 120000}), run_id),
    )
    # n1 完整事件
    k1 = _cache_key("q1", {"model": None})
    j.append_event(run_id, "a_n1", "n1", "started", k1, {"label": "n1"})
    j.append_event(run_id, "a_n1", "n1", "result", k1,
                   {"label": "n1", "status": "success", "output": "o1"})
    # n2 仅 started(半完成)
    k2 = _cache_key("q2", {"model": None})
    j.append_event(run_id, "a_n2", "n2", "started", k2, {"label": "n2"})
    j._conn.commit()

    factory = _CountingFactory({"q2": "o2"})
    monkeypatch.setattr(wf_mod_pkg, "build_native_agent", factory)

    # resume 必须不 raise(n2 重跑时 started 二次写被吞)
    outcome = run_async(j.resume(run_id, engine))
    assert outcome["status"] == "resumed"
    assert outcome["cached"] == 1
    assert outcome["fresh"] == 1
    assert factory.build_count == 1

    # 验证:n2 最终有 result 事件(重跑成功),且 started 仍只有 1 条(无重复)
    events_final = j.list_events(run_id)
    n2_started = [e for e in events_final if e.node_label == "n2" and e.type == "started"]
    n2_results = [e for e in events_final if e.node_label == "n2" and e.type == "result"]
    assert len(n2_started) == 1, "started duplicate prevented by UNIQUE INDEX + swallowed"
    assert len(n2_results) == 1, "n2 result written on rerun"
    assert n2_results[0].payload["status"] == "success"
    j.close()
