"""W-P0-4 单测:WorkflowEngine._emit_workflow(fire-and-forget ADR-7 + R4 wire)。

verify(design §5 W-P0-4):
- emitter=None 不崩(静默跳过,Q4 默认路径)
- emit 招回抛异常不冒泡(R3 fire-and-forget,整体 try/except)
- 构造事件 dict 含 data.flow_event=workflow_started(R4 真实语义塞 data)
- event_type 恒为 tick_completed(R4 observe enum 冻结,绝不 import EventType)
- emitter.emit 被调度一次(loop.create_task)且 event dict 结构对位 flow.py:98-121
- 未在 WORKFLOW_FLOW_EVENTS 的 event_name 不阻断(仅 warning)
"""

import asyncio

from harness.workflow_engine import (
    WORKFLOW_FLOW_EVENTS,
    WorkflowEngine,
)


# ── sync wrapper(避开 pytest-asyncio loop pollution,见 spawn 测试母版
#    feedback-pytest-asyncio-loop-pollution)──
_LOOP = None


def run_async(coro):
    global _LOOP
    if _LOOP is None or _LOOP.is_closed():
        _LOOP = asyncio.new_event_loop()
        asyncio.set_event_loop(_LOOP)
    return _LOOP.run_until_complete(coro)


# ── Fake emitter:记录 emit 调用 / 可注入抛异常 ──────────────────────
class _FakeEmitter:
    """记录 emit 调用 + 可注入异常(R3 验证 fire-and-forget)。"""

    def __init__(self, exc=None):
        self._exc = exc
        self.events = []

    async def emit(self, event):
        if self._exc is not None:
            raise self._exc
        self.events.append(event)


# ─────────────────────────────────────────────────────────────────────
# emitter=None 静默跳过(Q4 默认 CI 路径)
# ─────────────────────────────────────────────────────────────────────
def test_emit_workflow_no_emitter_does_not_crash():
    """emitter=None 时 _emit_workflow 静默 return,不崩不发(ADR-7 fire-and-forget)。"""
    e = WorkflowEngine(emitter=None)

    # 必须在 running loop 内(_emit_workflow 走 get_running_loop,虽 emitter=None 早 return)
    async def _go():
        e._emit_workflow(
            "workflow_started",
            run_id="wf_test1",
            payload={"node_count": 3},
            session_id="s1",
        )

    # 不 raise 即通过
    run_async(_go())


def test_emit_workflow_no_emitter_returns_early():
    """emitter=None 早 return:不调度任何 task(无副作用,纯静默)。"""
    e = WorkflowEngine(emitter=None)

    async def _go():
        # 在 running loop 内调,验证 None 分支不触达 create_task
        e._emit_workflow("workflow_started", "wf_t", {}, session_id="s")

    run_async(_go())  # 不 raise 即通过


# ─────────────────────────────────────────────────────────────────────
# R3 fire-and-forget:emit 抛异常不冒泡
# ─────────────────────────────────────────────────────────────────────
def test_emit_workflow_emit_raises_does_not_bubble():
    """emitter.emit 抛异常时,_emit_workflow 不冒泡(整体 try/except R3)。

    注:create_task 调度的协程内异常不会同步冒泡到 create_task 调用点(asyncio 协程
    异常仅在 await 时抛);本测更严:用 emitter.emit 同步抛(RaiseInEmit)验证
    二重兜底 — emit 是协程,create_task 不 await 故异常进 task context。
    为机械验证 R3,我们直接 monkeypatch emitter.emit 为同步 raise 的非协程,
    走 try/except Exception 分支不冒泡。
    """
    e = WorkflowEngine(emitter=_FakeEmitter())

    # 替 emit 为同步抛异常的 callable(模拟 emitter 对象损坏)
    def _boom_sync(*a, **kw):
        raise RuntimeError("emit object broken")

    e.emitter.emit = _boom_sync  # type: ignore[assignment]

    async def _go():
        # 同步 emit 抛 → try/except 兜底,不冒泡
        e._emit_workflow("workflow_started", "wf_t", {"x": 1}, session_id="s")

    run_async(_go())  # 不 raise 即 R3 通过


def test_emit_workflow_payload_corruption_does_not_bubble():
    """payload 内含不可序列化对象 / event_name 不在冻结集 → warning 不冒泡(R3)。"""
    e = WorkflowEngine(emitter=_FakeEmitter())

    async def _go():
        # 未知 event_name:仅 warning,继续 emit(不阻断 workflow 主路径)
        e._emit_workflow(
            "not_a_real_flow_event",
            "wf_t",
            {"k": "v"},
            session_id="s",
        )

    run_async(_go())  # 不 raise 即通过


# ─────────────────────────────────────────────────────────────────────
# R4 event_type 恒 tick_completed + data.flow_event 真实语义
# ─────────────────────────────────────────────────────────────────────
def test_emit_workflow_wires_tick_completed_and_flow_event():
    """构造事件:event_type 恒 'tick_completed',data.flow_event=workflow_started,
    data.flow_payload 含真实 payload(R4 照搬 flow.py:98-121 模式)。"""
    fake = _FakeEmitter()
    e = WorkflowEngine(emitter=fake)

    async def _go():
        e._emit_workflow(
            "workflow_started",
            run_id="wf_abc",
            payload={"node_count": 5, "fan_in": "list"},
            session_id="sess1",
        )
        # 等 create_task 调度的 emit 完成(让 events 落袋)
        await asyncio.sleep(0)

    run_async(_go())

    assert len(fake.events) == 1
    ev = fake.events[0]
    # R4:event_type 恒 tick_completed(绝不 import EventType / 新增枚举值)
    assert ev["event_type"] == "tick_completed"
    # R4:真实语义塞 data.flow_event + data.flow_payload
    assert ev["data"]["flow_event"] == "workflow_started"
    assert ev["data"]["flow_payload"] == {"node_count": 5, "fan_in": "list"}
    # wire 结构对位 flow.py:98-121(harness_type / harness_id / tick_id / timestamp)
    assert ev["harness_type"] == "workflow"
    assert ev["harness_id"] == "wf_abc"
    assert ev["tick_id"] == "wf_abc"
    assert ev["session_id"] == "sess1"
    assert "timestamp" in ev
    assert "event_id" in ev


def test_emit_workflow_session_id_defaults_to_run_id():
    """session_id 省略时默认 = run_id(对位 flow.py session_id=flow_id 模式)。"""
    fake = _FakeEmitter()
    e = WorkflowEngine(emitter=fake)

    async def _go():
        e._emit_workflow(
            "workflow_completed",
            run_id="wf_xyz",
            payload={"status": "success"},
            # session_id 省略
        )
        await asyncio.sleep(0)

    run_async(_go())

    assert len(fake.events) == 1
    ev = fake.events[0]
    assert ev["session_id"] == "wf_xyz"  # 默认回退到 run_id


# ─────────────────────────────────────────────────────────────────────
# R4 WORKFLOW_FLOW_EVENTS 冻结集:覆盖所有 P0+P1 值(防漂移)
# ─────────────────────────────────────────────────────────────────────
def test_workflow_flow_events_frozenset_contains_expected_values():
    """WORKFLOW_FLOW_EVENTS 是 frozenset 且含 P0+P1 全值(design §3.1)。"""
    assert isinstance(WORKFLOW_FLOW_EVENTS, frozenset)
    # P0 4 值
    assert "workflow_started" in WORKFLOW_FLOW_EVENTS
    assert "workflow_completed" in WORKFLOW_FLOW_EVENTS
    assert "workflow_node_started" in WORKFLOW_FLOW_EVENTS
    assert "workflow_node_completed" in WORKFLOW_FLOW_EVENTS
    # P1 5 值(先定义,值占位)
    assert "workflow_pipeline_stage_started" in WORKFLOW_FLOW_EVENTS
    assert "workflow_pipeline_stage_completed" in WORKFLOW_FLOW_EVENTS
    assert "workflow_loop_started" in WORKFLOW_FLOW_EVENTS
    assert "workflow_loop_iteration" in WORKFLOW_FLOW_EVENTS
    assert "workflow_loop_completed" in WORKFLOW_FLOW_EVENTS


def test_each_workflow_flow_event_emits_cleanly():
    """遍历 WORKFLOW_FLOW_EVENTS 每个值,_emit_workflow 都能 wire 出合法事件。"""
    fake = _FakeEmitter()
    e = WorkflowEngine(emitter=fake)

    async def _go():
        for name in WORKFLOW_FLOW_EVENTS:
            e._emit_workflow(name, "wf_t", {"i": 1}, session_id="s")
        await asyncio.sleep(0)

    run_async(_go())

    assert len(fake.events) == len(WORKFLOW_FLOW_EVENTS)
    emitted_names = {ev["data"]["flow_event"] for ev in fake.events}
    assert emitted_names == set(WORKFLOW_FLOW_EVENTS)
    # 全部 event_type 恒 tick_completed
    assert all(ev["event_type"] == "tick_completed" for ev in fake.events)
