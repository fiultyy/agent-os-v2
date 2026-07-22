"""W-P0-2 单测:WorkflowEngine.__init__ + _spawn_agent(单一 spawn chokepoint)。

verify(design §5 W-P0-2):
- monkeypatch build_native_agent 返 fake agent
- fake agent.run raise → _spawn_agent 返 NodeResult(status='error') 不 raise(R3 降级)
- 成功路径返 status='success' + usage 填充(RK2 per-node RunUsage)
- caps 列表零记忆写入 capability(R1 守恒 — grep 守恒另在 redline 自检跑)
- build_native_agent 未传工程纪律 capability 实例(R5 — 默认 prepend 路径,验证 caps 不含)
"""

import asyncio

import harness.workflow_engine as wf_mod
from harness.workflow_engine import (
    WorkflowContext,
    WorkflowEngine,
    WorkflowNodeSpec,
)

# ── sync wrapper(避开 pytest-asyncio loop pollution,见 memory
#    feedback-pytest-asyncio-loop-pollution + test_neural_field.run_async 母版)──
_LOOP = None


def run_async(coro):
    global _LOOP
    if _LOOP is None or _LOOP.is_closed():
        _LOOP = asyncio.new_event_loop()
        asyncio.set_event_loop(_LOOP)
    return _LOOP.run_until_complete(coro)


def _ctx():
    return WorkflowContext(session_id="s1", agent_id_prefix="wf", run_id="wf_test1")


# ── Fake native Agent:run 返固定 output 或 raise ──────────────────────
class _FakeRunResult:
    def __init__(self, output):
        self.output = output


class _FakeAgent:
    def __init__(self, output="ok", exc=None):
        self._output = output
        self._exc = exc
        self.run_called_with = None  # 记录调用参数(usage=)

    async def run(self, task_input, *, usage=None, usage_limits=None):
        self.run_called_with = {"task_input": task_input, "usage": usage}
        if self._exc is not None:
            raise self._exc
        return _FakeRunResult(self._output)


# ── monkeypatch helper:替 workflow_engine 模块里的 build_native_agent ──
class _BuildRecorder:
    """记录 build_native_agent 调用 + 返 fake agent。返回固定 fake。"""

    def __init__(self, fake_agent):
        self.fake_agent = fake_agent
        self.last_capabilities = None
        self.last_instructions = None
        self.last_model_name = None
        self.last_output_type = None
        self.call_count = 0

    def __call__(self, instructions="", capabilities=None, toolsets=None,
                 model_settings=None, model_name=None, mcp_servers=None,
                 output_type=None):
        self.call_count += 1
        self.last_instructions = instructions
        self.last_capabilities = list(capabilities) if capabilities else []
        self.last_model_name = model_name
        self.last_output_type = output_type
        return self.fake_agent


# ─────────────────────────────────────────────────────────────────────
# __init__ 注入
# ─────────────────────────────────────────────────────────────────────
def test_init_stores_deps():
    e = WorkflowEngine(emitter="EM", pitfail_registry="PF", tool_executor="TE")
    assert e.emitter == "EM"
    assert e.pitfail_registry == "PF"
    assert e.tool_executor == "TE"


def test_init_defaults_none():
    e = WorkflowEngine()
    assert e.emitter is None
    assert e.pitfail_registry is None
    assert e.tool_executor is None


# ─────────────────────────────────────────────────────────────────────
# 成功路径:status='success' + usage 填充 + output 透传
# ─────────────────────────────────────────────────────────────────────
def test_spawn_agent_success_path():
    from pydantic_ai.usage import RunUsage

    fake = _FakeAgent(output="hello")
    rec = _BuildRecorder(fake)
    orig = wf_mod.build_native_agent
    wf_mod.build_native_agent = rec
    try:
        e = WorkflowEngine()
        node = WorkflowNodeSpec.model_validate({"prompt": "do task", "label": "n1"})
        result = run_async(e._spawn_agent(node, _ctx()))
    finally:
        wf_mod.build_native_agent = orig

    assert result.status == "success"
    assert result.label == "n1"
    assert result.output == "hello"
    assert result.error is None
    # RK2:per-node RunUsage 传入 agent.run 且 NodeResult.usage 是同一对象
    assert isinstance(result.usage, RunUsage)
    assert fake.run_called_with["usage"] is result.usage
    # agent_id 带前缀
    assert result.agent_id.startswith("wf_")


# ─────────────────────────────────────────────────────────────────────
# R3 降级:agent.run raise → NodeResult(status='error') 不 raise
# ─────────────────────────────────────────────────────────────────────
def test_spawn_agent_run_raises_degrades_to_error():
    fake = _FakeAgent(exc=RuntimeError("boom"))
    rec = _BuildRecorder(fake)
    orig = wf_mod.build_native_agent
    wf_mod.build_native_agent = rec
    try:
        e = WorkflowEngine()
        node = WorkflowNodeSpec.model_validate({"prompt": "task", "label": "n2"})
        # 不 raise — 降级返 NodeResult
        result = run_async(e._spawn_agent(node, _ctx()))
    finally:
        wf_mod.build_native_agent = orig

    assert result.status == "error"
    assert "boom" in (result.error or "")
    assert result.label == "n2"
    assert result.output is None


# ─────────────────────────────────────────────────────────────────────
# R6 空串占位:node.prompt 非空时透传(WorkflowNodeSpec.min_length=1 已兜底,
# 但 _spawn_agent 内 task_input = node.prompt or "(no task input)" 仍验证)
# ─────────────────────────────────────────────────────────────────────
def test_spawn_agent_passes_prompt_as_task_input():
    fake = _FakeAgent(output="ok")
    rec = _BuildRecorder(fake)
    orig = wf_mod.build_native_agent
    wf_mod.build_native_agent = rec
    try:
        e = WorkflowEngine()
        node = WorkflowNodeSpec.model_validate({"prompt": "the actual prompt"})
        run_async(e._spawn_agent(node, _ctx()))
    finally:
        wf_mod.build_native_agent = orig

    assert fake.run_called_with["task_input"] == "the actual prompt"


# ─────────────────────────────────────────────────────────────────────
# R1 守恒:caps 组装零记忆写入 capability
# ─────────────────────────────────────────────────────────────────────
def test_spawn_agent_caps_no_memory_writer():
    fake = _FakeAgent(output="ok")
    rec = _BuildRecorder(fake)
    orig = wf_mod.build_native_agent
    wf_mod.build_native_agent = rec
    try:
        e = WorkflowEngine()
        node = WorkflowNodeSpec.model_validate({"prompt": "task"})
        run_async(e._spawn_agent(node, _ctx()))
    finally:
        wf_mod.build_native_agent = orig

    cap_class_names = [type(c).__name__ for c in rec.last_capabilities]
    # ToolBridgeCapability 必有(子 agent 工具桥)
    assert "ToolBridgeCapability" in cap_class_names
    # R1:绝不挂 MemoryWriterCapability / MemoryCapability
    assert "MemoryWriterCapability" not in cap_class_names
    assert not any("Memory" in n for n in cap_class_names if n != "ObserveCapability")


# ─────────────────────────────────────────────────────────────────────
# R5 守恒:caps 未含工程纪律 capability(走默认 prepend,native_agent.py:114)
# ─────────────────────────────────────────────────────────────────────
def test_spawn_agent_caps_no_engineering_discipline():
    fake = _FakeAgent(output="ok")
    rec = _BuildRecorder(fake)
    orig = wf_mod.build_native_agent
    wf_mod.build_native_agent = rec
    try:
        e = WorkflowEngine()
        node = WorkflowNodeSpec.model_validate({"prompt": "task"})
        run_async(e._spawn_agent(node, _ctx()))
    finally:
        wf_mod.build_native_agent = orig

    cap_class_names = [type(c).__name__ for c in rec.last_capabilities]
    assert not any("Engineering" in n or "Discipline" in n for n in cap_class_names)


# ─────────────────────────────────────────────────────────────────────
# ObserveCapability 可选:emitter=None 不挂;emitter 通电挂上
# ─────────────────────────────────────────────────────────────────────
def test_spawn_agent_no_observe_when_emitter_none():
    fake = _FakeAgent(output="ok")
    rec = _BuildRecorder(fake)
    orig = wf_mod.build_native_agent
    wf_mod.build_native_agent = rec
    try:
        e = WorkflowEngine(emitter=None)  # emitter 未通电
        node = WorkflowNodeSpec.model_validate({"prompt": "task"})
        run_async(e._spawn_agent(node, _ctx()))
    finally:
        wf_mod.build_native_agent = orig

    cap_class_names = [type(c).__name__ for c in rec.last_capabilities]
    assert "ToolBridgeCapability" in cap_class_names
    assert "ObserveCapability" not in cap_class_names  # emitter=None 不挂


def test_spawn_agent_observe_when_emitter_present():
    from harness.capabilities import ObserveCapability

    fake = _FakeAgent(output="ok")
    rec = _BuildRecorder(fake)
    orig = wf_mod.build_native_agent
    wf_mod.build_native_agent = rec
    try:
        e = WorkflowEngine(emitter=object())  # truthy emitter
        node = WorkflowNodeSpec.model_validate({"prompt": "task"})
        run_async(e._spawn_agent(node, _ctx()))
    finally:
        wf_mod.build_native_agent = orig

    observe_caps = [c for c in rec.last_capabilities if isinstance(c, ObserveCapability)]
    assert len(observe_caps) == 1
    assert observe_caps[0].session_id == "s1"


# ─────────────────────────────────────────────────────────────────────
# RK2 per-node RunUsage:两次 spawn 不共享 usage 对象
# ─────────────────────────────────────────────────────────────────────
def test_spawn_agent_per_node_usage_not_shared():
    fake1 = _FakeAgent(output="a")
    fake2 = _FakeAgent(output="b")
    fake_iter = iter([fake1, fake2])
    rec = _BuildRecorder(None)
    rec.__call__ = lambda *a, **kw: next(fake_iter)  # type: ignore[method-assign]
    orig = wf_mod.build_native_agent
    wf_mod.build_native_agent = rec
    try:
        e = WorkflowEngine()
        n1 = WorkflowNodeSpec.model_validate({"prompt": "t1"})
        n2 = WorkflowNodeSpec.model_validate({"prompt": "t2"})
        r1 = run_async(e._spawn_agent(n1, _ctx()))
        r2 = run_async(e._spawn_agent(n2, _ctx()))
    finally:
        wf_mod.build_native_agent = orig

    # per-node RunUsage:两 spawn 的 usage 是独立对象(非共享可变引用)
    assert r1.usage is not r2.usage


# ─────────────────────────────────────────────────────────────────────
# model_name 透传:node.model → build_native_agent(model_name=...)
# ─────────────────────────────────────────────────────────────────────
def test_spawn_agent_passes_model_name():
    fake = _FakeAgent(output="ok")
    rec = _BuildRecorder(fake)
    orig = wf_mod.build_native_agent
    wf_mod.build_native_agent = rec
    try:
        e = WorkflowEngine()
        node = WorkflowNodeSpec.model_validate({"prompt": "task", "model": "glm-4-flash"})
        run_async(e._spawn_agent(node, _ctx()))
    finally:
        wf_mod.build_native_agent = orig

    assert rec.last_model_name == "glm-4-flash"


# ─────────────────────────────────────────────────────────────────────
# 真 wiring 烟雾测:不 monkeypatch build_native_agent,跑到 caps 组装
# (真 ToolBridgeCapability / ObserveCapability 构造),堵 fake-build 漂移
# (skeptic major #1:kwarg 名漂移致真 spawn TypeError,11 mock 单测全盲)。
# ponytail:仅验证 caps 阶段不崩,不下到 agent.run(避免真 LLM 依赖)。
# ─────────────────────────────────────────────────────────────────────
def test_spawn_agent_real_capability_construction_smoke():
    """真 ToolBridgeCapability(...)/ObserveCapability(...) 构造不抛
    TypeError(unexpected keyword argument 'pitfail_registry'/'emitter')。

    _spawn_agent 内的 caps 组装跑真 dataclass 构造,暴露字段名漂移。
    注入 emitter truthy 让 ObserveCapability 分支也走真构造。
    """
    from harness.capabilities import ObserveCapability, ToolBridgeCapability
    import dataclasses

    # 真 ObserveCapability 需要 emitter,用一个最小 stub 对象
    class _EmitterStub:
        def emit(self, *a, **kw):
            pass

    # 真 ToolBridgeCapability / ObserveCapability 构造不抛 TypeError
    tc = ToolBridgeCapability(tool_executor=None, pitfail_registry=None)
    assert dataclasses.is_dataclass(tc)
    assert tc.pitfail_registry is None

    oc = ObserveCapability(
        emitter=_EmitterStub(),
        harness_id="wf_smoke",
        session_id="s1",
    )
    assert oc.session_id == "s1"

    # WorkflowEngine 注入 pitfail_registry → caps 组装字段命中
    e = WorkflowEngine(emitter=_EmitterStub(), pitfail_registry="PF", tool_executor="TE")
    assert e.pitfail_registry == "PF"  # param↔field 对齐(major #1 真坑)


# ─────────────────────────────────────────────────────────────────────
# P2 schema-registry wiring:node.schema_ref → resolve_schema → output_type
# ─────────────────────────────────────────────────────────────────────
def _recorder_for(fake_agent):
    rec = _BuildRecorder(fake_agent)
    orig = wf_mod.build_native_agent
    wf_mod.build_native_agent = rec
    return rec, orig


def test_spawn_agent_schema_ref_registered_passes_output_type():
    """schema_ref 命中已注册 schema → build_native_agent 收到 output_type=MyModel,
    NodeResult.output 是该 model 实例(structured output 透传)。"""
    from pydantic import BaseModel

    from harness.workflow_engine import _SCHEMA_REGISTRY, register_schema

    class _MyModel(BaseModel):
        answer: str
        score: int

    register_schema("my_schema", _MyModel)
    try:
        expected = _MyModel(answer="hi", score=7)
        fake = _FakeAgent(output=expected)
        rec, orig = _recorder_for(fake)
        try:
            e = WorkflowEngine()
            node = WorkflowNodeSpec.model_validate(
                {"prompt": "task", "schema_ref": "my_schema"}
            )
            result = run_async(e._spawn_agent(node, _ctx()))
        finally:
            wf_mod.build_native_agent = orig

        assert rec.last_output_type is _MyModel
        assert result.status == "success"
        assert isinstance(result.output, _MyModel)
        assert result.output.answer == "hi"
        assert result.output.score == 7
    finally:
        _SCHEMA_REGISTRY.pop("my_schema", None)


def test_spawn_agent_schema_ref_text_passthrough():
    """schema_ref='text' → resolve_schema 返 None → output_type=None(str 默认)。"""
    fake = _FakeAgent(output="plain str")
    rec, orig = _recorder_for(fake)
    try:
        e = WorkflowEngine()
        node = WorkflowNodeSpec.model_validate({"prompt": "task", "schema_ref": "text"})
        result = run_async(e._spawn_agent(node, _ctx()))
    finally:
        wf_mod.build_native_agent = orig

    assert rec.last_output_type is None
    assert result.output == "plain str"


def test_spawn_agent_schema_ref_none_passthrough():
    """schema_ref=None(默认)→ output_type=None(str 默认)。"""
    fake = _FakeAgent(output="plain")
    rec, orig = _recorder_for(fake)
    try:
        e = WorkflowEngine()
        node = WorkflowNodeSpec.model_validate({"prompt": "task"})
        result = run_async(e._spawn_agent(node, _ctx()))
    finally:
        wf_mod.build_native_agent = orig

    assert rec.last_output_type is None
    assert result.output == "plain"


def test_spawn_agent_schema_ref_unknown_degrades_to_str():
    """schema_ref 未注册 → resolve_schema 返 None → 降级 str passthrough(不崩)。"""
    fake = _FakeAgent(output="fallback")
    rec, orig = _recorder_for(fake)
    try:
        e = WorkflowEngine()
        node = WorkflowNodeSpec.model_validate(
            {"prompt": "task", "schema_ref": "never_registered"}
        )
        result = run_async(e._spawn_agent(node, _ctx()))
    finally:
        wf_mod.build_native_agent = orig

    assert rec.last_output_type is None
    assert result.status == "success"
    assert result.output == "fallback"
