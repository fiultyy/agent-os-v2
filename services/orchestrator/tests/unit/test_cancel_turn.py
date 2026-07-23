"""T1(ADR-O6):cancel 端点协作式中断测试。

验证三契约:
- tick_id 不在 _async_turn_tasks → 404 JSON(已结束/从未存在)
- tick_id 在 → task.cancel() 调用 + 从 registry 移除 + emit tick_completed(cancelled)
- 响应 {status: cancelled, tick_id}

不碰 workflow_engine(R1);observe 侧只经 emitter.emit(不引 memory,R5)。
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from src.harness import routes


def _seed_async_turn(monkeypatch, *, tick_id="tick-1"):
    """塞一个可 cancel 的真 asyncio.Task 进 _async_turn_tasks(需在事件循环内调)。

    task 跑一个永不自结束的 sleep(只能被 .cancel() 打断),验 cancel 真调用。
    """
    async def _hang():
        await asyncio.sleep(100)

    task = asyncio.create_task(_hang())
    monkeypatch.setattr(routes, "_async_turn_tasks", {tick_id: task})
    return task


def _patch_session_rec(monkeypatch, emitter_events: list):
    """Patch routes._sessions 持一个带 emitter 的 native rec(cancel 端点取 emitter)。"""
    fake_emitter = MagicMock()
    fake_emitter.emit = MagicMock(
        side_effect=lambda ev: emitter_events.append(ev) or asyncio.sleep(0))

    rec = {
        "emitter": fake_emitter,
        "harness_id": "native_s1",
        "session_id": "s1",
        "harness_type": "agent-os-v2",
    }
    monkeypatch.setattr(
        routes, "_sessions",
        {routes._key("agent-os-v2", "s1"): rec})
    return rec


# ── tick_id 不在 registry → 404 ──────────────────────────────────────────

def test_cancel_unknown_tick_returns_404(monkeypatch):
    monkeypatch.setattr(routes, "_async_turn_tasks", {})
    _patch_session_rec(monkeypatch, [])

    with pytest.raises(HTTPException) as ei:
        asyncio.run(routes.cancel_turn(
            "agent-os-v2", "s1",
            routes.CancelTurnReq(tick_id="nope"),
        ))
    assert ei.value.status_code == 404
    assert "nope" in str(ei.value.detail)


# ── tick 在 → cancel + 移除 + emit tick_completed(cancelled) ──────────────

def test_cancel_known_tick_cancels_and_emits(monkeypatch):
    emitter_events: list = []
    _patch_session_rec(monkeypatch, emitter_events)

    async def go():
        task = _seed_async_turn(monkeypatch, tick_id="tick-1")
        assert not task.cancelled()

        resp = await routes.cancel_turn(
            "agent-os-v2", "s1",
            routes.CancelTurnReq(tick_id="tick-1"),
        )

        # 响应契约
        assert resp == {"session_id": "s1", "tick_id": "tick-1", "status": "cancelled"}
        # 等 task 真落到 cancelled 态
        await asyncio.gather(task, return_exceptions=True)
        assert task.cancelled()
        # registry 移除
        assert "tick-1" not in routes._async_turn_tasks
        # emit tick_completed(status=cancelled) 经 observe WS
        assert len(emitter_events) == 1
        ev = emitter_events[0]
        assert ev["event_type"] == "tick_completed"
        assert ev["data"]["status"] == "cancelled"
        assert ev["tick_id"] == "tick-1"
        assert ev["session_id"] == "s1"

    asyncio.run(go())


# ── emitter 缺失(rec 无 emitter/已删)→ task 仍 cancel,emit 跳过不崩 ──────

def test_cancel_without_emitter_still_cancels(monkeypatch):
    monkeypatch.setattr(
        routes, "_sessions",
        {routes._key("agent-os-v2", "s1"): {"harness_id": "native_s1"}})

    async def go():
        task = _seed_async_turn(monkeypatch, tick_id="t2")
        resp = await routes.cancel_turn(
            "agent-os-v2", "s1",
            routes.CancelTurnReq(tick_id="t2"),
        )
        assert resp["status"] == "cancelled"
        await asyncio.gather(task, return_exceptions=True)
        assert task.cancelled()
        assert "t2" not in routes._async_turn_tasks

    asyncio.run(go())


# ── 无效 harness_type → 400(复用 _validate_type) ─────────────────────────

def test_cancel_invalid_type_400(monkeypatch):
    monkeypatch.setattr(routes, "_async_turn_tasks", {"t3": MagicMock()})
    with pytest.raises(HTTPException) as ei:
        asyncio.run(routes.cancel_turn(
            "bogus", "s1", routes.CancelTurnReq(tick_id="t3")))
    assert ei.value.status_code == 400


# ── Fix-1:tick_id 单域(async turn tick_started == cancel tick_completed) ─

def test_async_turn_tick_id_single_domain(monkeypatch):
    """Fix-1(方案 A):trigger_turn(async_run=True) 注入的 orchestration tick_id
    经 agent.run(metadata={"tick_id":...}) 进 ObserveCapability → emit 的 tick_started
    与 cancel 端点 emit 的 tick_completed(cancelled) 同 tick_id。

    真 ObserveCapability + 真 emitter(收集事件)+ fake agent.run 记录 metadata,
    验注入链路(routes._run_native_turn_async → agent.run metadata → cap ctx.metadata)。
    """
    from src.harness.capabilities import ObserveCapability

    observed: list = []  # ObserveCapability emit 的事件(tick_started/tool_call/...)

    class _FakeResult:
        def __init__(self):
            self.output = "ok"
            self.usage = MagicMock(input_tokens=1, output_tokens=1, cache_read_tokens=0)
        def all_messages(self):
            return []

    class _FakeAgent:
        """记录 metadata 供断言;不强依赖 pydantic-ai graph(cap 层单测覆盖映射)。"""
        def __init__(self):
            self.run_calls = []
        async def run(self, message, message_history=None, metadata=None):
            self.run_calls.append({"message": message, "metadata": metadata})
            return _FakeResult()

    fake_agent = _FakeAgent()

    # emitter:ObserveCapability 经此 emit observe 事件(收集 tick_started)
    fake_emitter = MagicMock()
    fake_emitter.emit = MagicMock(side_effect=lambda ev: observed.append(ev) or asyncio.sleep(0))

    cap = ObserveCapability(
        emitter=fake_emitter, harness_id="native_s1", session_id="s1", agent_id="native")
    rec = {
        "agent": fake_agent,
        "emitter": fake_emitter,
        "messages": [],
        "session_id": "s1",
        "harness_type": "agent-os-v2",
        "native_sid": "s1",
        "cwd_scope": [],
        "spec_id": "native",
        # 不含 capability(cap 直接驱动测 tick_id 注入路径,不依赖 graph)
    }
    monkeypatch.setattr(
        routes, "_sessions", {routes._key("agent-os-v2", "s1"): rec})
    monkeypatch.setattr(routes, "_async_turn_tasks", {})

    async def go():
        # 1) trigger async turn → orchestration tick_id 生成 + background agent.run
        resp = await routes.trigger_turn(
            "agent-os-v2", "s1",
            routes.TurnReq(message="hello", async_run=True),
        )
        assert resp["status"] == "started"
        orch_tick_id = resp["tick_id"]
        assert orch_tick_id, "orchestration tick_id must be non-empty"

        # 2) background task 跑完(此处 fake agent 无 cap,不 emit tick_started;
        #    验 metadata 注入链路:_run_native_turn_async 真传了 tick_id)
        await asyncio.gather(*routes._async_turn_tasks.values())
        assert fake_agent.run_calls, "background agent.run must run"
        injected_meta = fake_agent.run_calls[0]["metadata"]
        assert injected_meta == {"tick_id": orch_tick_id}, (
            "agent.run must receive metadata={'tick_id': <orchestration tick_id>}; "
            f"got {injected_meta!r}")

        # 3) 直接驱动 ObserveCapability 验注入的 tick_id 真能穿透到 emit 的事件。
        #    (routes 异步路径不经 graph,故 cap 不自动 wrap;手动用注入的 tick_id 驱动,
        #     模拟真实 wrap_run_event_stream 经 ctx.metadata 读到的同 tick_id。)
        from pydantic_ai import FunctionToolCallEvent
        from pydantic_ai.messages import ToolCallPart

        class _Ctx:
            metadata = {"tick_id": orch_tick_id}
        async def stream():
            yield FunctionToolCallEvent(ToolCallPart("t", {}, "c1"))
        async def _drive():
            async for _ in cap.wrap_run_event_stream(ctx=_Ctx(), stream=stream()):
                pass
        await _drive()

        tick_started_ev = next(
            (e for e in observed if e["event_type"] == "tick_started"), None)
        assert tick_started_ev is not None, "tick_started must be emitted"
        assert tick_started_ev["tick_id"] == orch_tick_id, (
            "ObserveCapability tick_started tick_id must equal orchestration tick_id")

        # 4) cancel 端点 emit tick_completed(cancelled) 用同 orch_tick_id(单域断言)
        #    重新塞 task(cancel 已 pop 过;造一个新的可 cancel task 模拟在跑)
        async def _hang():
            await asyncio.sleep(100)
        cancel_task = asyncio.create_task(_hang())
        monkeypatch.setattr(routes, "_async_turn_tasks", {orch_tick_id: cancel_task})

        cancel_resp = await routes.cancel_turn(
            "agent-os-v2", "s1",
            routes.CancelTurnReq(tick_id=orch_tick_id),
        )
        assert cancel_resp == {"session_id": "s1", "tick_id": orch_tick_id,
                               "status": "cancelled"}

        cancel_completed = next(
            (e for e in observed
             if e["event_type"] == "tick_completed" and e["data"]["status"] == "cancelled"),
            None)
        assert cancel_completed is not None, "tick_completed(cancelled) must be emitted"
        # 核心单域断言:cancel 的 tick_completed 与 tick_started 同 tick_id
        assert cancel_completed["tick_id"] == tick_started_ev["tick_id"], (
            "tick_started and tick_completed(cancelled) MUST share tick_id "
            "(single-domain invariant); got "
            f"started={tick_started_ev['tick_id']!r} cancelled={cancel_completed['tick_id']!r}")

        await asyncio.gather(cancel_task, return_exceptions=True)

    asyncio.run(go())
