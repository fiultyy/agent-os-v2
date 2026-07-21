"""W-P2-1 单测:workflow_engine/journal.py WorkflowJournal(SQLite 事件溯源 resume)。

verify(design §5 W-P2-1):
- (a) 中断在第 2 node started 后 result 前 → resume 重跑该 node(半完成 agent 重跑)。
- (b) 已完成 node(有 result 事件)走 cache(resume 不重跑)。
- (c) UNIQUE INDEX (run_id, key, type) 防 cache hit 重复写(INSERT 冲突抛
  IntegrityError)— 幂等保证兜底。

+ 顺带覆盖:
- start_run 幂等(INSERT OR IGNORE)。
- mark_completed 写回 status / total_usage / finished_at。
- fetch_run 不存在 → None。
- resume 已 completed run → replay_skip。
- resume 未知 run_id → ValueError。
- usage_to_json / usage_from_json round-trip(pydantic-ai 2.0 无 from_json)。
- _cache_key 稳定(prompt+opts 一致 → key 一致)。
"""

import asyncio
import json
import os
import sqlite3
import sys
import tempfile

import pytest
from pydantic_ai.usage import RunUsage

from harness.workflow_engine import (
    NodeResult,
    WorkflowContext,
    WorkflowEngine,
    WorkflowNodeSpec,
    WorkflowNodesSpec,
)
from harness.workflow_engine.journal import (
    JOURNAL_SCHEMA,
    WorkflowEvent,
    WorkflowJournal,
    _cache_key,
    _merge_cached_usage,
    usage_from_json,
    usage_to_json,
)

# ── sync wrapper(避开 pytest-asyncio loop pollution,见 spawn 测试母版)──
_LOOP = None


def run_async(coro):
    global _LOOP
    if _LOOP is None or _LOOP.is_closed():
        _LOOP = asyncio.new_event_loop()
        asyncio.set_event_loop(_LOOP)
    return _LOOP.run_until_complete(coro)


# ── Fake native Agent(同 test_workflow_engine_run 母版)────────────────
class _FakeRunResult:
    def __init__(self, output):
        self.output = output


class _FakeAgent:
    def __init__(self, output="ok", usage=None):
        self._output = output
        self._usage = usage or RunUsage(input_tokens=5, output_tokens=3, requests=1)
        self.run_calls = 0

    async def run(self, task_input, usage=None, usage_limits=None):
        self.run_calls += 1
        if isinstance(self._output, Exception):
            raise self._output
        # 把 fake usage 写入调用者传的 usage 对象(per-node RunUsage)
        if usage is not None:
            usage.incr(self._usage)
        return _FakeRunResult(self._output)


def _build_fake_agent_factory(outputs_by_label):
    """返 build_native_agent monkeypatch 工厂:label → fake output(支持重跑)。"""
    def factory(instructions="", capabilities=None, model_name=None):
        # 用 closure 捕获 outputs_by_label;每次 build_native_agent 返新 fake agent
        # 按 label 找预期 output;未知 label 默认 'fallback'。
        # label 在 _spawn_agent 是 node.label,但 build_native_agent 见不到 label;
        # 故用 model_name 通道传(label 编进 instructions 不稳;改用 model_name= label hack)。
        # 简化:返一个 agent,fake.run 时按 task_input 匹配。
        return _RoutingFakeAgent(outputs_by_label)
    return factory


class _RoutingFakeAgent:
    """按 task_input 内容路由到对应 output(prompt == node.prompt)。"""

    def __init__(self, outputs_by_label):
        self._outputs = outputs_by_label

    async def run(self, task_input, usage=None, usage_limits=None):
        # task_input 是 node.prompt(或 "(no task input)" 兜底)
        for prompt, out in self._outputs.items():
            if prompt in task_input:
                if isinstance(out, Exception):
                    raise out
                if usage is not None:
                    usage.incr(RunUsage(input_tokens=5, output_tokens=3, requests=1))
                return _FakeRunResult(out)
        # 兜底
        if usage is not None:
            usage.incr(RunUsage(input_tokens=1, requests=1))
        return _FakeRunResult("fallback")


# ── 用 monkeypatch build_native_agent(同 test_workflow_engine_run 模式)──
@pytest.fixture
def patched_engine(monkeypatch):
    import harness.workflow_engine.engine as engine_mod
    engine = WorkflowEngine(emitter=None, pitfail_registry=None, tool_executor=None)
    return engine, engine_mod, monkeypatch


@pytest.fixture
def monkeypatch_pkg(monkeypatch):
    """package-level build_native_agent patch 目标(``harness.workflow_engine``)。

    ``_resolve_build_native_agent()`` 经包 namespace 取最新值(见 engine.py:32-42),
    故 patch 必须落包属性而非子模块属性(对位 integration 测试母版)。F3 用量断言
    需 fake agent 接管,避免真实 agent 的非确定性 usage 污染断言。
    """
    import harness.workflow_engine as wf_mod_pkg
    return wf_mod_pkg, monkeypatch


# ─────────────────────────────────────────────────────────────────────
# 基础:DDL / 连接 / start_run 幂等 / fetch_run / mark_completed
# ─────────────────────────────────────────────────────────────────────
def test_journal_creates_tables_and_indexes(tmp_path):
    db = tmp_path / "wf.db"
    j = WorkflowJournal(str(db))
    # 两表存在
    tables = {
        r[0] for r in j._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "workflow_run" in tables
    assert "workflow_event" in tables
    # UNIQUE INDEX 存在
    idx = {
        r[0] for r in j._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='workflow_event'"
        ).fetchall()
    }
    assert "uq_wf_event_key" in idx, "UNIQUE INDEX (run_id,key,type) must exist"
    j.close()


def test_start_run_is_idempotent():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    os.unlink(path)  # 让 journal 新建
    j = WorkflowJournal(path)
    spec_json = json.dumps({"nodes": [{"prompt": "hi", "label": "n1"}]})
    j.start_run("wf_test1", spec_json, "sess1")
    j.start_run("wf_test1", spec_json, "sess1")  # 二次调幂等
    row = j.fetch_run("wf_test1")
    assert row is not None
    assert row["session_id"] == "sess1"
    assert row["status"] == "running"
    assert json.loads(row["spec_json"])["nodes"][0]["label"] == "n1"
    j.close()
    os.unlink(path)


def test_fetch_run_unknown_returns_none(tmp_path):
    j = WorkflowJournal(str(tmp_path / "wf.db"))
    assert j.fetch_run("nonexistent") is None
    j.close()


def test_mark_completed_writes_status_and_usage():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    os.unlink(path)
    j = WorkflowJournal(path)
    j.start_run("wf_test2", "{}", "sess1")
    usage = RunUsage(input_tokens=10, output_tokens=4, requests=2)
    j.mark_completed("wf_test2", "completed", usage)
    row = j.fetch_run("wf_test2")
    assert row["status"] == "completed"
    assert row["finished_at"] is not None
    stored = json.loads(row["total_usage"])
    assert stored["input_tokens"] == 10
    assert stored["requests"] == 2
    j.close()
    os.unlink(path)


# ─────────────────────────────────────────────────────────────────────
# cache key + RunUsage round-trip
# ─────────────────────────────────────────────────────────────────────
def test_cache_key_stable():
    k1 = _cache_key("hello", {"model": "glm"})
    k2 = _cache_key("hello", {"model": "glm"})
    k3 = _cache_key("hello", {"model": "claude"})
    assert k1 == k2
    assert k1 != k3, "different opts must yield different cache key"
    assert k1.startswith("v2:")
    assert len(k1) == len("v2:") + 16


def test_usage_round_trip():
    u = RunUsage(input_tokens=10, output_tokens=20, requests=2)
    s = usage_to_json(u)
    u2 = usage_from_json(s)
    assert u2.input_tokens == 10
    assert u2.output_tokens == 20
    assert u2.requests == 2
    # computed property
    assert u2.total_tokens == 30


def test_usage_from_json_none_yields_fresh():
    u = usage_from_json(None)
    assert isinstance(u, RunUsage)
    assert u.total_tokens == 0


def test_usage_from_json_garbage_yields_fresh():
    u = usage_from_json("not json{")
    assert isinstance(u, RunUsage)
    assert u.total_tokens == 0


# ─────────────────────────────────────────────────────────────────────
# (c) UNIQUE INDEX 防 cache hit 重复写(INSERT 冲突)
# ─────────────────────────────────────────────────────────────────────
def test_unique_index_blocks_duplicate_event():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    os.unlink(path)
    j = WorkflowJournal(path)
    j.start_run("wf_test3", "{}", "sess1")
    key = _cache_key("prompt_a", {"model": "glm"})
    j.append_event("wf_test3", "agent_X", "n1", "result", key, {"label": "n1"})
    # 同 (run_id, key, type) 再写 → IntegrityError
    with pytest.raises(sqlite3.IntegrityError):
        j.append_event("wf_test3", "agent_X2", "n1", "result", key, {"label": "n1"})
    j.close()
    os.unlink(path)


def test_unique_index_allows_started_and_result_same_key():
    """同一 key 的 started + result 两条不冲突(不同 type)。"""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    os.unlink(path)
    j = WorkflowJournal(path)
    j.start_run("wf_test4", "{}", "sess1")
    key = _cache_key("prompt_b", {})
    e1 = j.append_event("wf_test4", "agent_Y", "n1", "started", key, {"label": "n1"})
    e2 = j.append_event("wf_test4", "agent_Y", "n1", "result", key, {"label": "n1"})
    assert e1.type == "started"
    assert e2.type == "result"
    events = j.list_events("wf_test4")
    assert len(events) == 2
    j.close()
    os.unlink(path)


def test_append_event_seq_monotonic():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    os.unlink(path)
    j = WorkflowJournal(path)
    j.start_run("wf_test5", "{}", "sess1")
    e1 = j.append_event("wf_test5", "a1", "n1", "started", "k1", {})
    e2 = j.append_event("wf_test5", "a1", "n1", "result", "k1", {})
    e3 = j.append_event("wf_test5", "a2", "n2", "started", "k2", {})
    assert (e1.seq, e2.seq, e3.seq) == (0, 1, 2)
    j.close()
    os.unlink(path)


# ─────────────────────────────────────────────────────────────────────
# resume(事件溯源重放)
# ─────────────────────────────────────────────────────────────────────
def test_resume_unknown_run_id_raises():
    j = WorkflowJournal(":memory:")
    # :memory: 跨连接丢表;WorkflowJournal 单 conn OK
    # 但 __init__ 用单 conn,重开丢 — 这里复用同实例
    with pytest.raises(ValueError, match="unknown run_id"):
        run_async(j.resume("nonexistent", engine=None))


def test_resume_completed_run_skipped():
    j = WorkflowJournal(":memory:")
    j.start_run("wf_done", "{}", "sess1")
    j.mark_completed("wf_done", "completed", RunUsage())
    res = run_async(j.resume("wf_done", engine=None))
    assert res["status"] == "replay_skip"
    assert "completed" in res["reason"]


def _node_key(prompt, model=None):
    """镜像 ``engine._journal_append`` 的 cache key 计算(opts={"model": node.model})。

    F4:resume 按 cache key 去重后,seed 的 event.key 必须与 ``_spawn_agent`` 实际
    写入的一致(``_cache_key(prompt, {"model": node.model})``),否则 resume 端算
    的 node key 与 seed 写入的 event.key 不撞 → 误判全 pending。default model=None
    对位 ``WorkflowNodeSpec.model`` 默认值。
    """
    return _cache_key(prompt, {"model": model})


def _seed_partial_run(j, run_id, completed_labels, partial_label):
    """构造中断场景:completed_labels 有 started+result;partial_label(可 None)仅 started。

    completed node 的 result payload 含 ``usage`` 字段(对位 engine.py:422-430
    success 分支 ``"usage": _usage_dict(node_usage)``)— F3 resume cached usage
    重建依赖此字段。每 completed node 贡献固定 usage(input=7,output=4,requests=1)。

    F4:spec node 默认 model=None,event.key 经 ``_node_key(prompt)`` 与 engine 实际
    写入路径(``_cache_key(prompt, {"model": None})``)对齐,确保 resume 按 key 命中。
    """
    all_labels = list(completed_labels) + ([partial_label] if partial_label else [])
    nodes = [{"prompt": f"prompt_{lab}", "label": lab} for lab in all_labels]
    j._conn.execute(
        "UPDATE workflow_run SET spec_json=? WHERE run_id=?",
        (
            json.dumps({"nodes": nodes, "fan_in": "list", "timeout_per_node_ms": 120000}),
            run_id,
        ),
    )
    j._conn.commit()
    # 写入 completed 节点的 started + result(含 usage,对位 engine 真实事件结构)
    for lab in completed_labels:
        prompt = f"prompt_{lab}"
        key = _node_key(prompt)
        j.append_event(run_id, f"agent_{lab}", lab, "started", key, {"label": lab})
        j.append_event(
            run_id, f"agent_{lab}", lab, "result", key,
            {
                "label": lab,
                "status": "success",
                "output": f"out_{lab}",
                "usage": {"input_tokens": 7, "output_tokens": 4, "requests": 1},
            },
        )
    # 仅 started 的半完成 node
    if partial_label is not None:
        prompt = f"prompt_{partial_label}"
        key = _node_key(prompt)
        j.append_event(
            run_id, f"agent_{partial_label}", partial_label, "started", key,
            {"label": partial_label},
        )


def test_resume_reruns_partial_and_caches_completed(patched_engine, tmp_path):
    """(a) 中断在 node2 started 后 result 前 → resume 重跑 node2;(b) node1 走 cache。"""
    engine, engine_mod, monkeypatch = patched_engine
    db = str(tmp_path / "wf.db")
    j = WorkflowJournal(db)
    run_id = "wf_partial1"
    j.start_run(run_id, "{}", "sess1")
    _seed_partial_run(j, run_id, completed_labels=["n1"], partial_label="n2")

    # monkeypatch build_native_agent:每个 prompt 返固定 output。
    # n1 已 cache,resume 不重跑(不应进入 fake agent);n2 重跑 → "out_n2"。
    outputs = {"prompt_n2": "out_n2"}
    monkeypatch.setattr(
        engine_mod, "build_native_agent",
        lambda **kw: _RoutingFakeAgent(outputs),
    )

    res = run_async(j.resume(run_id, engine))
    assert res["status"] == "resumed"
    assert res["cached"] == 1, "n1 should be cache hit"
    assert res["fresh"] == 1, "n2 should be rerun"
    # 终态写回
    row = j.fetch_run(run_id)
    assert row["status"] == "completed"
    j.close()


# ─────────────────────────────────────────────────────────────────────
# F3:resume 重建 ctx.total_usage 必须含 cached agent 用量(事件流重建)
# ─────────────────────────────────────────────────────────────────────
def test_merge_cached_usage_sums_success_events():
    """``_merge_cached_usage`` 仅累加 success result 事件的 usage 字段。"""
    j = WorkflowJournal(":memory:")
    j.start_run("wf_f3a", "{}", "sess1")
    key1 = _cache_key("p1", {})
    key2 = _cache_key("p2", {})
    key3 = _cache_key("p3", {})
    j.append_event("wf_f3a", "a1", "n1", "result", key1, {
        "label": "n1", "status": "success", "usage": {"input_tokens": 10, "output_tokens": 4, "requests": 1},
    })
    j.append_event("wf_f3a", "a2", "n2", "result", key2, {
        "label": "n2", "status": "success", "usage": {"input_tokens": 5, "output_tokens": 1, "requests": 1},
    })
    # error node 不计 usage(对位 engine error 分支无 usage 字段)
    j.append_event("wf_f3a", "a3", "n3", "result", key3, {
        "label": "n3", "status": "error", "error": "boom",
    })
    events = j.list_events("wf_f3a")
    total = _merge_cached_usage(events)
    assert total.input_tokens == 15
    assert total.output_tokens == 5
    assert total.requests == 2
    j.close()


def test_merge_cached_usage_missing_field_tolerant():
    """result payload 缺 usage 字段 / usage=None → 该节点贡献 0(防御性)。"""
    j = WorkflowJournal(":memory:")
    j.start_run("wf_f3b", "{}", "sess1")
    key = _cache_key("p", {})
    j.append_event("wf_f3b", "a", "n", "result", key,
                   {"label": "n", "status": "success"})  # 无 usage 字段
    total = _merge_cached_usage(j.list_events("wf_f3b"))
    assert total.input_tokens == 0
    assert total.requests == 0
    j.close()


def test_resume_total_usage_includes_cached(patched_engine, monkeypatch_pkg, tmp_path):
    """F3 验收:中断 run(total_usage NULL)resume 后 ctx.total_usage = cached + fresh。

    cached n1 贡献 {input=7,output=4,requests=1}(seed);fresh n2 重跑贡献
    {input=5,output=3,requests=1}(_RoutingFakeAgent)。resume 返回的 total_usage
    与终态行 total_usage 均应 = 两 node 累加(无偏低)。修前:cached 丢失,total
    偏低(仅 fresh)。

    注:patch 必须落 package-level(``harness.workflow_engine.build_native_agent``)
    非 ``engine_mod.build_native_agent`` —— ``_resolve_build_native_agent()`` 经包
    namespace 取最新值(见 integration 测试母版)。
    """
    engine, _engine_mod, _monkeypatch = patched_engine
    wf_mod_pkg, monkeypatch = monkeypatch_pkg
    db = str(tmp_path / "wf.db")
    j = WorkflowJournal(db)
    run_id = "wf_f3_resume"
    j.start_run(run_id, "{}", "sess1")
    _seed_partial_run(j, run_id, completed_labels=["n1"], partial_label="n2")
    # row.total_usage 为 NULL(中断 run 未调 mark_completed)— F3 触发条件
    assert j.fetch_run(run_id)["total_usage"] is None

    outputs = {"prompt_n2": "out_n2"}
    monkeypatch.setattr(
        wf_mod_pkg, "build_native_agent",
        lambda **kw: _RoutingFakeAgent(outputs),
    )

    res = run_async(j.resume(run_id, engine))
    assert res["status"] == "resumed"
    tu = res["total_usage"]
    # cached(n1: input=7) + fresh(n2: input=5) = 12;修前此处会偏低到 5(仅 fresh)
    assert tu["input_tokens"] == 12, f"cached usage lost: got {tu}"
    assert tu["output_tokens"] == 7  # 4(cached) + 3(fresh)
    assert tu["requests"] == 2

    # 终态行 mark_completed 也写了完整 total_usage(不只 resume 路径)
    row = j.fetch_run(run_id)
    stored = json.loads(row["total_usage"])
    assert stored["input_tokens"] == 12
    assert stored["requests"] == 2
    j.close()


def test_resume_all_completed_no_fresh(patched_engine, tmp_path):
    """全 node 都有 result → cached=N, fresh=0(不调 engine.run)。"""
    engine, engine_mod, monkeypatch = patched_engine
    db = str(tmp_path / "wf.db")
    j = WorkflowJournal(db)
    run_id = "wf_all_done"
    j.start_run(run_id, "{}", "sess1")
    _seed_partial_run(j, run_id, completed_labels=["a", "b"], partial_label=None)

    # engine.run 调用计数器(确保 fresh=0 时 run 不被调)
    call_count = {"n": 0}

    class _NeverRunEngine:
        async def run(self, spec, ctx):
            call_count["n"] += 1
            from harness.workflow_engine import WorkflowResult
            return WorkflowResult(
                status="success", node_results=[], total_usage=ctx.total_usage,
                elapsed_ms=0, node_count=0, run_id=ctx.run_id,
            )

    res = run_async(j.resume(run_id, _NeverRunEngine()))
    assert res["status"] == "resumed"
    assert res["cached"] == 2
    assert res["fresh"] == 0
    assert call_count["n"] == 0, "engine.run should not be called when fresh=0"
    j.close()


def test_resume_reruns_unstarted_node(patched_engine, tmp_path):
    """未启动 node(无任何事件)也进 pending 重跑。"""
    engine, engine_mod, monkeypatch = patched_engine
    db = str(tmp_path / "wf.db")
    j = WorkflowJournal(db)
    run_id = "wf_one_started_one_unstarted"
    j.start_run(run_id, "{}", "sess1")
    # seed spec:两 node,只写 n1 完整事件,n2 无任何事件
    nodes = [
        {"prompt": "p_n1", "label": "n1"},
        {"prompt": "p_n2", "label": "n2"},
    ]
    j._conn.execute("UPDATE workflow_run SET spec_json=? WHERE run_id=?", (
        json.dumps({"nodes": nodes, "fan_in": "list", "timeout_per_node_ms": 120000}),
        run_id,
    ))
    j._conn.commit()
    key1 = _node_key("p_n1")
    j.append_event(run_id, "a_n1", "n1", "started", key1, {"label": "n1"})
    j.append_event(run_id, "a_n1", "n1", "result", key1,
                   {"label": "n1", "status": "success", "output": "out_n1"})

    outputs = {"p_n2": "out_n2"}
    monkeypatch.setattr(
        engine_mod, "build_native_agent",
        lambda **kw: _RoutingFakeAgent(outputs),
    )
    res = run_async(j.resume(run_id, engine))
    assert res["cached"] == 1
    assert res["fresh"] == 1
    j.close()


# ─────────────────────────────────────────────────────────────────────
# F4:resume 按 cache key 去重(非 label);同名 label 不同 prompt 不碰撞
# ─────────────────────────────────────────────────────────────────────
def test_resume_default_label_no_collision(patched_engine, tmp_path):
    """F4 验收:2 node 共用默认 label="node" 但不同 prompt → 各自判 cached/pending。

    修前:resume 按 ``node.label not in cached_labels`` 去重,默认 label 都是
    "node" → 任一 cached 则全部误判 cached,pending_nodes 漏算(本场景会把 node_B
    也算 cached,fresh=0,不重跑)。
    修后:按 cache key(``_cache_key(prompt, {"model": node.model})``)去重,node_A
    cached(node_A prompt 的 result 事件存在),node_B pending(无对应 prompt 的
    result)→ fresh=1 重跑 node_B。

    spec 两 node 均不指定 label(走 ``WorkflowNodeSpec.label`` 默认 "node")+ 不指定
    model(走默认 None)→ 仅 prompt 区分;cache key 精确命中 node_A,不误伤 node_B。
    """
    engine, engine_mod, monkeypatch = patched_engine
    db = str(tmp_path / "wf.db")
    j = WorkflowJournal(db)
    run_id = "wf_f4_default_label"
    j.start_run(run_id, "{}", "sess1")
    # 两 node 均默认 label="node"(spec 不显式给 label),不同 prompt
    nodes = [
        {"prompt": "prompt_A"},   # label 省略 → 默认 "node"
        {"prompt": "prompt_B"},   # label 省略 → 默认 "node"
    ]
    j._conn.execute("UPDATE workflow_run SET spec_json=? WHERE run_id=?", (
        json.dumps({"nodes": nodes, "fan_in": "list", "timeout_per_node_ms": 120000}),
        run_id,
    ))
    j._conn.commit()
    # node_A 有完整 result 事件(走 cache);node_B 无任何事件(走 pending 重跑)
    key_a = _node_key("prompt_A")
    j.append_event(run_id, "agent_A", "node", "started", key_a, {"label": "node"})
    j.append_event(run_id, "agent_A", "node", "result", key_a, {
        "label": "node", "status": "success", "output": "out_A",
        "usage": {"input_tokens": 7, "output_tokens": 4, "requests": 1},
    })

    outputs = {"prompt_B": "out_B"}
    monkeypatch.setattr(
        engine_mod, "build_native_agent",
        lambda **kw: _RoutingFakeAgent(outputs),
    )

    res = run_async(j.resume(run_id, engine))
    assert res["status"] == "resumed"
    # 修前:fresh=0(误判 node_B 也 cached,label 撞)
    assert res["cached"] == 1, "only node_A prompt's key is cached"
    assert res["fresh"] == 1, "node_B must rerun (different prompt → different key)"
    j.close()


# ─────────────────────────────────────────────────────────────────────
# 包化 re-export 兼容(防回归)
# ─────────────────────────────────────────────────────────────────────
def test_package_reexport_backward_compat():
    """包化后 from harness.workflow_engine import 仍可用(engine.py 全部符号)。"""
    import harness.workflow_engine as wf_mod
    assert hasattr(wf_mod, "WorkflowEngine")
    assert hasattr(wf_mod, "WorkflowNodesSpec")
    assert hasattr(wf_mod, "NodeResult")
    assert hasattr(wf_mod, "PipelineSpec")
    assert hasattr(wf_mod, "LoopSpec")
    assert hasattr(wf_mod, "WORKFLOW_FLOW_EVENTS")
    assert hasattr(wf_mod, "_fan_in")
    assert hasattr(wf_mod, "_usage_dict")
    # 子模块可寻址
    from harness.workflow_engine import journal as journal_mod
    assert hasattr(journal_mod, "WorkflowJournal")
