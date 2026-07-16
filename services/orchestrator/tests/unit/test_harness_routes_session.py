"""routes.py session 管理单测(方案 B+C):create 落库 / list 从 store / turn 自动
resume / delete 同步 / restore 分派重建。

mock ClaudeClient/OpenClawClient(避免真连 observe/gateway/spawn claude)。
native_sid 端到端回填由 curl 全链验证(需真跑 `claude -p` 拿 result 事件的 sid)。
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.harness import routes
from src.harness.session_store import OrchSessionStore


@pytest.fixture
def store(tmp_path, monkeypatch):
    s = OrchSessionStore(str(tmp_path / "orch.db"))
    monkeypatch.setattr(routes, "_store", s)
    monkeypatch.setattr(routes, "_sessions", {})
    return s


def _fake_cc(native_sid=None):
    c = MagicMock()
    c.native_sid = native_sid
    c.running = False
    c.turn = AsyncMock(return_value={"tick_id": "t1", "status": "started"})
    c.delete = AsyncMock(return_value={"deleted": True})
    return c


def test_create_claude_session_persists_native_none(store, monkeypatch):
    monkeypatch.setattr(routes, "_create_claude", AsyncMock(return_value=_fake_cc()))
    sid = asyncio.run(
        routes.create_session("claude-code", routes.CreateSessionReq(cwd="/tmp/p"))
    )["session_id"]
    row = store.get("claude-code", sid)
    assert row is not None
    assert row["native_sid"] is None          # 首 turn 前为 NULL
    assert row["cwd"] == "/tmp/p"


def test_create_claw_session_native_equals_ext(store, monkeypatch):
    monkeypatch.setattr(routes, "_create_claw", AsyncMock(return_value=MagicMock(running=True)))
    sid = asyncio.run(
        routes.create_session("claw", routes.CreateSessionReq(agent_id="main"))
    )["session_id"]
    assert sid == "agent:main:main"
    assert store.get("claw", sid)["native_sid"] == sid   # claw native = ext


def test_list_sessions_reads_from_store(store, monkeypatch):
    # 模拟重启:store 有记录但 _sessions 空(内存丢)
    store.create("ghost_sid", "claude-code", native_sid="uuid-1", cwd="/x")
    out = asyncio.run(routes.list_sessions("claude-code"))
    assert out["count"] == 1
    s = out["sessions"][0]
    assert s["session_id"] == "ghost_sid"
    assert s["native_sid"] == "uuid-1"
    assert s["running"] is False              # 无内存 client → 不 running


def test_turn_auto_resume_only_when_native_present(store, monkeypatch):
    fake = _fake_cc(native_sid=None)
    monkeypatch.setattr(routes, "_create_claude", AsyncMock(return_value=fake))
    sid = asyncio.run(routes.create_session("claude-code", routes.CreateSessionReq()))["session_id"]

    # 首 turn:native None → resume=False(oneshot 建原生 session)
    asyncio.run(routes.trigger_turn("claude-code", sid, routes.TurnReq(message="hi")))
    assert fake.turn.call_args.kwargs["resume"] is False

    # 模拟首 turn 回填 native(实际由 on_native_sid 回调触发)
    fake.native_sid = "native-uuid-111"
    # 第二 turn:native 有 → 自动 resume=True(续聊原生 session)
    asyncio.run(routes.trigger_turn("claude-code", sid, routes.TurnReq(message="again")))
    assert fake.turn.call_args.kwargs["resume"] is True


def test_delete_syncs_store(store, monkeypatch):
    monkeypatch.setattr(routes, "_create_claude", AsyncMock(return_value=_fake_cc()))
    monkeypatch.setattr(routes, "_observe_delete_session", AsyncMock(return_value=True))
    sid = asyncio.run(routes.create_session("claude-code", routes.CreateSessionReq()))["session_id"]
    assert store.get("claude-code", sid) is not None
    asyncio.run(routes.delete_session("claude-code", sid))
    assert store.get("claude-code", sid) is None


def test_restore_all_sessions_rebuilds_from_store(store, monkeypatch):
    # 预填 store(claude + claw),模拟重启后内存空
    store.create("cc1", "claude-code", native_sid="uuid-cc1", cwd="/a")
    store.create("cl1", "claw", native_sid="agent:main:main", agent_id="main")
    monkeypatch.setattr(routes, "_create_claude",
                        AsyncMock(return_value=_fake_cc(native_sid="uuid-cc1")))
    monkeypatch.setattr(routes, "_create_claw",
                        AsyncMock(return_value=MagicMock(running=True)))

    restored = asyncio.run(routes.restore_all_sessions())
    assert restored["claude-code"] == 1
    assert restored["claw"] == 1
    assert routes._sessions[routes._key("claude-code", "cc1")]["native_sid"] == "uuid-cc1"
    assert routes._sessions[routes._key("claw", "cl1")]["native_sid"] == "agent:main:main"
