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
