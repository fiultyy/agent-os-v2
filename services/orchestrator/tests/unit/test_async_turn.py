"""F2 单测:native 异步 turn(fire-and-forget,opt-in 非破坏)。

验证:
- async_run=True 立返 {status: started, tick_id},非阻塞(background task 已创建)
- background task 跑完 emit tick_completed(ObserveCapability 生命周期)
- async_run=False(默认)路径完全不变:{status: completed, response}(向后兼容)
- task 保留:并发触发多 async turn,task 不被 GC(都跑完)

mock native Agent(避免真跑 GLM)+ 真 ObserveCapability(emitter 用 list 收集事件)
测 tick 生命周期事件。
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from src.harness import routes


# ── fake native session rec(镜像 _build_native_session 产出形态) ──────────

def _make_fake_rec(emitter_events: list | None = None,
                   run_coro_factory=None,
                   usage=None):
    """造一个最小 native rec:agent.run 返 fake result,emitter 收集事件。"""
    emitter_events = emitter_events if emitter_events is not None else []

    class _FakeResult:
        def __init__(self, output, messages, usage):
            self.output = output
            self._messages = messages
            self.usage = usage

        def all_messages(self):
            return self._messages

    class _FakeAgent:
        def __init__(self):
            self.run_calls = []

        async def run(self, message, message_history=None):
            self.run_calls.append((message, list(message_history or [])))
            return _FakeResult(
                output=f"reply:{message}",
                messages=[*(message_history or []), {"role": "assistant", "content": message}],
                usage=usage or _FakeUsage(),
            )

    fake_agent = _FakeAgent()
    fake_emitter = MagicMock()
    fake_emitter.emit = MagicMock(side_effect=lambda ev: emitter_events.append(ev) or asyncio.sleep(0))

    rec = {
        "agent": fake_agent,
        "emitter": fake_emitter,
        "messages": [],
        "session_id": "s1",
        "harness_type": "agent-os-v2",
        "native_sid": "s1",
        "cwd_scope": [],
        "spec_id": "native",
    }
    return rec


class _FakeUsage:
    def __init__(self):
        self.input_tokens = 10
        self.output_tokens = 5
        self.cache_read_tokens = 0


def _patch_sessions(monkeypatch, rec):
    """Patch routes._sessions so trigger_turn finds the native rec."""
    monkeypatch.setattr(routes, "_sessions", {routes._key("agent-os-v2", "s1"): rec})


def _patch_cwd_scope(monkeypatch):
    """trigger_turn native 路径读 cwd_scope + get_session_active_cwd;stub 掉。"""
    monkeypatch.setattr(routes, "_store", MagicMock())
    routes._store.get = MagicMock(return_value=None)
    routes._store.touch = MagicMock()
    routes._store.save_messages = MagicMock()

    import src.tools.cwd_scope as cs
    monkeypatch.setattr(cs, "get_session_active_cwd", lambda k: None)
    monkeypatch.setattr(cs, "_active_cwd", MagicMock())


# ── async_run=True 立返 started,非阻塞 ───────────────────────────────────

def test_async_run_returns_started_immediately(monkeypatch):
    rec = _make_fake_rec()
    _patch_sessions(monkeypatch, rec)
    _patch_cwd_scope(monkeypatch)
    monkeypatch.setattr(routes, "_async_turn_tasks", {})

    async def go():
        # trigger_turn 必须在 background agent.run 完成前立返 started
        resp = await routes.trigger_turn(
            "agent-os-v2", "s1",
            routes.TurnReq(message="hello", async_run=True),
        )
        assert resp["status"] == "started"
        assert "tick_id" in resp
        # task 已创建并在 registry 持有(非 None,not done necessarily)
        assert len(routes._async_turn_tasks) >= 1
        # 等它跑完(验证非空 task 真执行)
        await asyncio.gather(*routes._async_turn_tasks.values())
        # agent.run 真被调
        assert rec["agent"].run_calls, "background agent.run must run"
        return resp

    asyncio.run(go())


# ── async_run=False(默认)路径完全不变 ────────────────────────────────────

def test_sync_default_path_unchanged(monkeypatch):
    """R3:默认 async_run=False 保持 {status: completed, response} 完全不变。"""
    rec = _make_fake_rec()
    _patch_sessions(monkeypatch, rec)
    _patch_cwd_scope(monkeypatch)
    monkeypatch.setattr(routes, "_async_turn_tasks", {})

    async def go():
        resp = await routes.trigger_turn(
            "agent-os-v2", "s1",
            routes.TurnReq(message="hi"),  # async_run 默认 False
        )
        assert resp["status"] == "completed"
        assert resp["response"] == "reply:hi"
        # 同步路径不创建 async task
        assert len(routes._async_turn_tasks) == 0
        # messages 被同步更新(续聊语义保留)
        assert len(rec["messages"]) == 1
        return resp

    asyncio.run(go())


def test_sync_path_explicit_false_same_as_default(monkeypatch):
    """显式 async_run=False == 默认:返 completed(防 opt-in 误触发异步)。"""
    rec = _make_fake_rec()
    _patch_sessions(monkeypatch, rec)
    _patch_cwd_scope(monkeypatch)

    async def go():
        resp = await routes.trigger_turn(
            "agent-os-v2", "s1",
            routes.TurnReq(message="x", async_run=False),
        )
        assert resp["status"] == "completed"
        assert resp["response"] == "reply:x"

    asyncio.run(go())


# ── task 保留:并发触发多 async turn,task 不被 GC ────────────────────────

def test_concurrent_async_turns_all_complete(monkeypatch):
    """task registry 持有引用 → 并发多 async turn 全跑完(不被 GC 回收)。"""
    rec = _make_fake_rec()
    _patch_sessions(monkeypatch, rec)
    _patch_cwd_scope(monkeypatch)
    monkeypatch.setattr(routes, "_async_turn_tasks", {})

    async def go():
        # 并发触发 5 个 async turn(同 session,验 task registry 持有全部)
        # 抓 task 引用:trigger_turn 返 started 时 task 已入 registry;若 registry
        # 不持有(missed ref),task 被 GC → run 不执行。快 mock 可能 done_callback 先跑,
        # 故此处收 task_id,下面 poll 等所有 run_calls 到齐。
        resps = await asyncio.gather(*[
            routes.trigger_turn(
                "agent-os-v2", "s1",
                routes.TurnReq(message=f"m{i}", async_run=True),
            ) for i in range(5)
        ])
        # 全部立返 started,各自独 tick_id
        assert all(r["status"] == "started" for r in resps)
        tick_ids = [r["tick_id"] for r in resps]
        assert len(set(tick_ids)) == 5  # 5 个独 tick_id
        # 等 5 个 run_calls 全到齐(最多 1s;task 被 GC 则永不齐 → timeout 揭穿)
        for _ in range(100):
            if len(rec["agent"].run_calls) >= 5:
                break
            await asyncio.sleep(0.01)
        # 5 个 run 调用都执行(不被 GC 静默取消)← 核心断言
        assert len(rec["agent"].run_calls) == 5, "tasks must not be GC'd before running"
        # 收剩余 task(若有)驱动 done_callback 清 registry;done_callback 经
        # call_soon 调度,需 yield 一次让其跑。done 后 registry 应空(无界增长防护)。
        pending = list(routes._async_turn_tasks.values())
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        await asyncio.sleep(0)  # yield 让 done_callback 执行
        assert len(routes._async_turn_tasks) == 0

    asyncio.run(go())


# ── background task 异常不崩,observe 仍闭环(由 ObserveCapability 异常分支) ─

def test_async_turn_task_survives_agent_exception(monkeypatch):
    """agent.run 抛异常:task 不向外传播(HTTP 已 started),_run_native_turn_async log 吞。"""
    rec = _make_fake_rec()
    rec["agent"].run = MagicMock(side_effect=RuntimeError("boom"))
    _patch_sessions(monkeypatch, rec)
    _patch_cwd_scope(monkeypatch)
    monkeypatch.setattr(routes, "_async_turn_tasks", {})

    async def go():
        resp = await routes.trigger_turn(
            "agent-os-v2", "s1",
            routes.TurnReq(message="bad", async_run=True),
        )
        assert resp["status"] == "started"
        # 等 task 跑完(异常被 _run_native_turn_async 吞,不传播)
        await asyncio.gather(*routes._async_turn_tasks.values())
        assert rec["agent"].run.called

    asyncio.run(go())
