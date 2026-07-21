"""W-P0-5 单测:tools/composite/v2_workflow.py(workflow_run_handler 薄桥)。

verify(design §5 W-P0-5):
- handler 非法 nodes(空数组)返 ``{"status": "error", "error": "invalid nodes spec"}``
- 合法 nodes 薄桥调 ``WorkflowEngine.run(mock)`` — monkeypatch engine.run 记录调用,
  返预设 WorkflowResult,handler 返 ``{"status": <result.status>, "output": <dict>}``
- handler 返回状态化 dict(R7:返值非 raise;ToolExecutor 会再包一层 status:success)
- WORKFLOW_RUN_SCHEMA 含 minItems/minLength 等 model-facing 提示(RK7 双校验的
  JSON-schema 侧)
- ctx 字段透传:concurrency 透传 ctx.concurrency;run_id 模块级生成 ``wf_<hex12>``
- F5:handler 构造的 ctx.journal 非 None(此前恒 None 致 W-P2-4 journal 双写
  全 no-op / resume 不可用);run 后 workflow_event 表有 started+result 行;
  journal 构造失败时 ctx.journal=None 降级不崩(R3 fire-and-forget)
"""

import asyncio
import importlib.util
import os
from pathlib import Path

import harness.workflow_engine as wf_mod
from harness.workflow_engine import NodeResult, WorkflowResult
from harness.workflow_engine.journal import WorkflowJournal
from pydantic_ai.usage import RunUsage

# 直接按文件加载 v2_workflow.py,绕开 tools.composite.__init__(它会触发
# browser_flow → skills.browser 重依赖 import,与 P0 薄桥单测无关,且 browser_flow
# 的相对 import 在 test pythonpath 下抛 ImportError)。
_V2WF_PATH = (
    Path(__file__).resolve().parents[2]
    / "src" / "tools" / "composite" / "v2_workflow.py"
)
_spec = importlib.util.spec_from_file_location("v2_workflow_under_test", _V2WF_PATH)
assert _spec is not None and _spec.loader is not None
v2_workflow = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(v2_workflow)

WORKFLOW_RUN_SCHEMA = v2_workflow.WORKFLOW_RUN_SCHEMA
workflow_run_handler = v2_workflow.workflow_run_handler

# ── sync wrapper(避开 pytest-asyncio loop pollution;母版见 test_workflow_engine_run)──
_LOOP = None


def run_async(coro):
    global _LOOP
    if _LOOP is None or _LOOP.is_closed():
        _LOOP = asyncio.new_event_loop()
        asyncio.set_event_loop(_LOOP)
    return _LOOP.run_until_complete(coro)


def _fake_result(status="success", n=2):
    return WorkflowResult(
        status=status,
        node_results=[
            NodeResult(label=f"n{i}", agent_id=f"wf_abc_{i:08d}", output=f"o{i}")
            for i in range(n)
        ],
        total_usage=RunUsage(),
        elapsed_ms=42,
        node_count=n,
        run_id="wf_testrun",
        merged_output={"k": "v"} if status == "success" else None,
        errors=["err1"] if status == "error" else [],
    )


def _patch_engine_run(fake_result=None, capture=None):
    """monkeypatch WorkflowEngine.run 为 fake;返 restore。

    同步把 ``v2_workflow._build_journal`` 替为返 None 的 stub,避免现存 handler
    测试每次 tool call 在 worktree 根 data/ 落 orch_workflow.db 文件污染。
    """
    orig = wf_mod.WorkflowEngine.run
    orig_build_journal = v2_workflow._build_journal

    async def _fake_run(self, spec, ctx):
        if capture is not None:
            capture["spec"] = spec
            capture["ctx"] = ctx
        if fake_result is not None:
            return fake_result
        return _fake_result()

    wf_mod.WorkflowEngine.run = _fake_run
    v2_workflow._build_journal = lambda: None  # 存量 handler 测试不落盘

    def restore():
        wf_mod.WorkflowEngine.run = orig
        v2_workflow._build_journal = orig_build_journal

    return restore


# ─────────────────────────────────────────────────────────────────────
# RK7:非法 nodes(空数组)→ status='error', error='invalid nodes spec'
# ─────────────────────────────────────────────────────────────────────
def test_handler_empty_nodes_returns_invalid_spec_error():
    # Tool.from_schema any_schema 不校验 minItems,handler 首行 WorkflowNodesSpec
    # .model_validate 二次校验兜底 — 空 nodes 被 pydantic min_length=1 拒
    result = run_async(workflow_run_handler(nodes=[]))
    assert result["status"] == "error"
    # 逐字 design §2.2.3 文案(RK7 + R7)
    assert result["error"] == "invalid nodes spec"
    # 非法分支不调 engine.run,无 output 字段
    assert "output" not in result


def test_handler_prompt_empty_string_returns_invalid_spec_error():
    # prompt:'' 违反 WorkflowNodeSpec.prompt min_length=1(R6 入口兜底)
    result = run_async(workflow_run_handler(nodes=[{"prompt": ""}]))
    assert result["status"] == "error"
    assert result["error"] == "invalid nodes spec"


def test_handler_invalid_fan_in_returns_invalid_spec_error():
    # fan_in 非 'list'/'merge' 违反 Literal
    result = run_async(workflow_run_handler(
        nodes=[{"prompt": "hi"}], fan_in="bogus",  # type: ignore[arg-type]
    ))
    assert result["status"] == "error"
    assert result["error"] == "invalid nodes spec"


# ─────────────────────────────────────────────────────────────────────
# 合法 nodes:薄桥调 WorkflowEngine.run(mock),返状态化 dict
# ─────────────────────────────────────────────────────────────────────
def test_handler_valid_nodes_dispatches_engine_run():
    capture: dict = {}
    restore = _patch_engine_run(fake_result=_fake_result(status="success", n=2), capture=capture)
    try:
        result = run_async(workflow_run_handler(
            nodes=[{"prompt": "t1"}, {"prompt": "t2"}],
            fan_in="list",
            concurrency=4,
        ))
    finally:
        restore()

    # handler 返状态化 dict(顶层 status 来自 WorkflowResult.status)
    assert result["status"] == "success"
    assert "output" in result
    out = result["output"]
    # output 是 _result_to_dict 序列化的 WorkflowResult dict
    assert out["run_id"] == "wf_testrun"
    assert out["status"] == "success"
    assert out["node_count"] == 2
    assert len(out["node_results"]) == 2
    assert out["node_results"][0]["label"] == "n0"
    # total_usage 字段存在(_usage_dict 序列化)
    assert "total_usage" in out
    assert out["total_usage"]["requests"] == 0  # RunUsage() 零基线
    # F2:fan-in 字段序列化(merged_output / errors)—— 此前 handler 不暴露,对模型 no-op
    assert out["merged_output"] == {"k": "v"}
    assert out["errors"] == []

    # 薄桥确实调了 engine.run(spec, ctx)
    assert capture["spec"] is not None
    assert len(capture["spec"].nodes) == 2
    # concurrency 透传到 ctx
    assert capture["ctx"].concurrency == 4
    # run_id 模块级生成 wf_<hex12>
    assert capture["ctx"].run_id.startswith("wf_")
    assert len(capture["ctx"].run_id) == 15  # 'wf_' + 12 hex


def test_handler_propagates_error_status_from_engine():
    """engine.run 返 status='error'(全 node 失败)→ handler 顶层 status='error'。"""
    restore = _patch_engine_run(fake_result=_fake_result(status="error", n=1))
    try:
        result = run_async(workflow_run_handler(nodes=[{"prompt": "boom"}]))
    finally:
        restore()
    assert result["status"] == "error"
    # output 仍含 WorkflowResult 详情(主 agent 可看 node error)
    assert result["output"]["status"] == "error"


def test_handler_engine_run_exception_returns_error_not_raise():
    """R7:engine.run raise 时 handler 状态化返 error,不冒泡(tool_executor 语义)。"""
    orig = wf_mod.WorkflowEngine.run

    async def _raise(self, spec, ctx):
        raise RuntimeError("engine imploded")

    wf_mod.WorkflowEngine.run = _raise
    try:
        # 不 raise — R7 返值非 raise
        result = run_async(workflow_run_handler(nodes=[{"prompt": "x"}]))
    finally:
        wf_mod.WorkflowEngine.run = orig

    assert result["status"] == "error"
    assert "engine.run" in result["error"]
    assert "engine imploded" in result["error"]


# ─────────────────────────────────────────────────────────────────────
# JSON Schema(RK7 model-facing 提示):关键字段存在
# ─────────────────────────────────────────────────────────────────────
def test_workflow_run_schema_has_rk7_constraints():
    """RK7 JSON-schema 侧:nodes minItems/maxItems + prompt minLength 提示模型。"""
    schema = WORKFLOW_RUN_SCHEMA
    assert schema["type"] == "object"
    assert schema["required"] == ["nodes"]
    assert schema["additionalProperties"] is False

    nodes_prop = schema["properties"]["nodes"]
    assert nodes_prop["minItems"] == 1
    assert nodes_prop["maxItems"] == 4096

    node_item = nodes_prop["items"]
    assert node_item["required"] == ["prompt"]
    assert node_item["properties"]["prompt"]["minLength"] == 1

    # fan_in / timeout / concurrency 约束
    assert schema["properties"]["fan_in"]["enum"] == ["list", "merge"]
    assert schema["properties"]["timeout_per_node_ms"]["minimum"] == 1000
    assert schema["properties"]["concurrency"]["minimum"] == 1
    assert schema["properties"]["concurrency"]["maximum"] == 64


# ─────────────────────────────────────────────────────────────────────
# 默认值:fan_in='list' / timeout=120000 / concurrency=8(design §2.2.1)
# ─────────────────────────────────────────────────────────────────────
def test_handler_defaults_match_design():
    capture: dict = {}
    restore = _patch_engine_run(capture=capture)
    try:
        run_async(workflow_run_handler(nodes=[{"prompt": "hi"}]))
    finally:
        restore()
    # spec 默认 fan_in='list' / timeout 120000(WorkflowNodesSpec 默认)
    assert capture["spec"].fan_in == "list"
    assert capture["spec"].timeout_per_node_ms == 120000
    # ctx.concurrency 默认 8(handler 默认参数)
    assert capture["ctx"].concurrency == 8


# ─────────────────────────────────────────────────────────────────────
# F5(review fix):handler 构造的 ctx.journal 非 None(journal 模块通电),
# 且 run 后 workflow_event 表有 started + result 行;db 不可用时降级 None 不崩(R3)。
# ─────────────────────────────────────────────────────────────────────
def test_handler_constructs_ctx_with_real_journal(tmp_path, monkeypatch):
    """F5 主断言:handler 构造的 ctx.journal 是 WorkflowJournal 实例(非 None)。

    修前 handler 未传 journal 致 ctx.journal 恒 None,engine.py:522-533 /
    _spawn_agent 的 _journal_append 全 no-op,journal 模块 speculative。
    db 落 tmp_path 避免污染 worktree。
    """
    db = str(tmp_path / "wf.db")

    # mock engine.run 但保留真 _build_journal → ctx 落 capture,断言其 .journal
    orig_run = wf_mod.WorkflowEngine.run
    capture: dict = {}

    async def _capture_run(self, spec, ctx):
        capture["ctx"] = ctx
        return _fake_result()

    monkeypatch.setattr(wf_mod.WorkflowEngine, "run", _capture_run)
    monkeypatch.setattr(v2_workflow, "_build_journal", lambda: WorkflowJournal(db))

    try:
        run_async(workflow_run_handler(nodes=[{"prompt": "hi"}]))
    finally:
        wf_mod.WorkflowEngine.run = orig_run

    ctx = capture["ctx"]
    # F5 核心:ctx.journal 非 None —— 此前恒 None 致整个 journal 模块 no-op
    assert ctx.journal is not None, "F5: handler must wire ctx.journal (was always None pre-fix)"
    assert isinstance(ctx.journal, WorkflowJournal)
    ctx.journal.close()


def test_handler_run_writes_started_and_result_rows(tmp_path, monkeypatch):
    """F5 e2e:handler 真跑(engine 未 mock)→ workflow_event 表落 started + result。

    复用 test_workflow_journal_integration 母版的 _RoutingFakeAgent — 真跑 engine
    证明 journal 双写在 handler 入口不再 no-op。db 落 tmp_path。
    """
    db = str(tmp_path / "wf_e2e.db")
    if os.path.exists(db):
        os.unlink(db)
    monkeypatch.setenv("ORCH_WORKFLOW_DB", db)

    # patch 包级 build_native_agent(engine.py:_resolve_build_native_agent 经包 namespace 取)
    class _FakeRunResult:
        def __init__(self, output):
            self.output = output

    class _RoutingFakeAgent:
        async def run(self, task_input, *, usage=None, usage_limits=None):
            return _FakeRunResult("out_" + ("a" if "pa" in task_input else "b"))

    monkeypatch.setattr(wf_mod, "build_native_agent", lambda **kw: _RoutingFakeAgent())

    result = run_async(workflow_run_handler(
        nodes=[{"prompt": "pa"}, {"prompt": "pb"}], fan_in="list",
    ))
    assert result["status"] == "success"
    run_id = result["output"]["run_id"]

    # 读 journal 表验证(journal 已 close 在 ctx 析构前,重开只读核对)
    j = WorkflowJournal(db)
    try:
        events = j.list_events(run_id)
        started = [e for e in events if e.type == "started"]
        results = [e for e in events if e.type == "result"]
        # 每 node 一对 started/result —— 修前 0 行(handler 未通电)
        assert len(started) == 2, f"expected 2 started rows, got {len(started)} (F5 wiring broken)"
        assert len(results) == 2, f"expected 2 result rows, got {len(results)} (F5 wiring broken)"
        # run 终态
        row = j.fetch_run(run_id)
        assert row["status"] == "completed"
    finally:
        j.close()


def test_handler_journal_construction_failure_degrades_to_none(monkeypatch):
    """F5 R3:_build_journal 失败(只读 FS / pysqlite3 缺)→ ctx.journal=None 降级,
    handler 不崩,run 正常返回(fire-and-forget,design §3 R3 对位 engine.py:522-533)。

    模拟方式:让 WorkflowJournal 构造 raise(经 _build_journal 内 try/except 兜底
    返 None — 测试真 R3 路径,不绕过 _build_journal 本身)。
    """
    def _boom(*a, **kw):
        raise RuntimeError("db unavailable (simulated)")

    # patch WorkflowJournal 类(_build_journal 内 try 块 import 后调用其构造)
    import harness.workflow_engine.journal as journal_mod
    monkeypatch.setattr(journal_mod, "WorkflowJournal", _boom)

    # mock engine.run 捕获 ctx,验证降级后 ctx.journal=None
    orig_run = wf_mod.WorkflowEngine.run
    capture: dict = {}

    async def _capture_run(self, spec, ctx):
        capture["ctx"] = ctx
        return _fake_result()

    monkeypatch.setattr(wf_mod.WorkflowEngine, "run", _capture_run)
    try:
        result = run_async(workflow_run_handler(nodes=[{"prompt": "hi"}]))
    finally:
        wf_mod.WorkflowEngine.run = orig_run

    # 降级不崩,handler 仍返 success(mock engine)
    assert result["status"] == "success"
    # R3 核心:journal 不可用时 ctx.journal=None,engine 侧 no-op,run 不崩
    assert capture["ctx"].journal is None, "F5 R3: journal construction failure must degrade to None"
