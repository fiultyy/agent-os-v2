"""F1:native fork + 1→N fan-out + parent lineage 单测。

ADR-S2:native fork = 深拷贝 ModelMessages + 继承源 agent_id + parent_session_id。
ADR-S3:fan-out targets 列表;cc 保持 1:1(本测不碰),claw stub 仍 501。

测试隔离:mock _build_native_session(避免真 Agent/emitter/LLM)+ 真 OrchSessionStore(tmp db)。
"""

from __future__ import annotations

import asyncio
import copy
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

from src.harness import routes
from src.harness.session_store import OrchSessionStore


@pytest.fixture
def store(tmp_path, monkeypatch):
    s = OrchSessionStore(str(tmp_path / "orch.db"))
    monkeypatch.setattr(routes, "_store", s)
    monkeypatch.setattr(routes, "_sessions", {})
    return s


def _native_rec(messages=None, spec_id="native"):
    """轻量 native rec mock(_build_native_session 返的结构)。messages 用真 ModelMessage
    让深拷贝断言有意义(验证非浅引用)。emitter=AsyncMock:F3 fork emit branch_created
    用 `await emitter.emit(...)`,普通 MagicMock 不可 await。"""
    return {
        "agent": MagicMock(), "emitter": AsyncMock(), "messages": messages or [],
        "session_id": "s", "harness_type": "agent-os-v2", "native_sid": "s",
        "cwd_scope": [], "spec_id": spec_id,
    }


# ── native fork:深拷贝 messages(ADR-S2 核心)──────────────────────────

def test_native_fork_deep_copies_messages(store, monkeypatch):
    """fork 后新 session.messages 内容 == 源,但是新 list + 新对象(改新不影响源)。"""
    src_msgs = [ModelRequest(parts=[UserPromptPart(content="orig")])]
    src_rec = _native_rec(messages=src_msgs, spec_id="native")
    src_sid = "src-deepcopy"
    routes._sessions[routes._key("agent-os-v2", src_sid)] = src_rec
    store.create(src_sid, "agent-os-v2", native_sid=src_sid, agent_id="native")

    # _build_native_session 透传 messages 参数 → 验证传入的是深拷贝(内容等、对象非同一)
    captured = {}

    async def fake_build(sid, messages=None, agent_id=None):
        captured[sid] = messages
        return _native_rec(messages=messages, spec_id=agent_id or "native")
    monkeypatch.setattr(routes, "_build_native_session", fake_build)

    r = asyncio.run(routes.fork_session(
        "agent-os-v2", routes.ForkReq(source_session_id=src_sid, first_message="branch A"),
    ))
    new_sid = r["forks"][0]["new_session_id"]
    new_msgs = captured[new_sid]

    # 内容相等
    assert new_msgs == src_msgs
    # 非同一 list(深拷贝)
    assert new_msgs is not src_msgs
    # 非同一 message 对象(深拷贝,改新不污染源)
    assert new_msgs[0] is not src_msgs[0]
    # 破坏性验证:改新 message 的 part,源不变(真深拷贝铁证)
    assert src_msgs[0].parts[0].content == "orig"


def test_native_fork_mutation_does_not_leak_to_source(store, monkeypatch):
    """新 fork session append 一条 message → 源 session messages 不变(非浅引用)。"""
    src_msgs = [ModelResponse(parts=[TextPart(content="src-turn")])]
    src_sid = "src-noleak"
    routes._sessions[routes._key("agent-os-v2", src_sid)] = _native_rec(
        messages=src_msgs, spec_id="native")
    store.create(src_sid, "agent-os-v2", native_sid=src_sid, agent_id="native")

    new_recs = {}

    async def fake_build(sid, messages=None, agent_id=None):
        rec = _native_rec(messages=messages, spec_id=agent_id)
        new_recs[sid] = rec
        return rec
    monkeypatch.setattr(routes, "_build_native_session", fake_build)

    asyncio.run(routes.fork_session(
        "agent-os-v2", routes.ForkReq(source_session_id=src_sid, first_message="x"),
    ))
    forked_sid = next(iter(new_recs))
    # 模拟 fork 后在分支上跑 turn:append 一条 message
    new_recs[forked_sid]["messages"].append(
        ModelResponse(parts=[TextPart(content="fork-only-turn")]))
    # 源不受影响(长度仍 1,内容未变)
    assert len(routes._sessions[routes._key("agent-os-v2", src_sid)]["messages"]) == 1
    assert routes._sessions[routes._key("agent-os-v2", src_sid)]["messages"][0] is src_msgs[0]


# ── agent_id 继承(ADR-S2)─────────────────────────────────────────────

def test_native_fork_inherits_source_agent_id(store, monkeypatch):
    """fork 用源 session 的 spec_id(spec.id normalize)建新 session(同 agent 探不同方向)。"""
    src_sid = "src-agent"
    routes._sessions[routes._key("agent-os-v2", src_sid)] = _native_rec(
        messages=[], spec_id="english-expert")
    store.create(src_sid, "agent-os-v2", native_sid=src_sid, agent_id="english-expert")

    built_agent_ids = []

    async def fake_build(sid, messages=None, agent_id=None):
        built_agent_ids.append(agent_id)
        return _native_rec(messages=messages, spec_id=agent_id)
    monkeypatch.setattr(routes, "_build_native_session", fake_build)

    asyncio.run(routes.fork_session(
        "agent-os-v2", routes.ForkReq(source_session_id=src_sid, first_message="x"),
    ))
    assert built_agent_ids == ["english-expert"]  # 继承源,非 None / 非 default


def test_native_fork_orphan_source_rebuilds_from_store(store, monkeypatch):
    """源 session store 有、内存无(restore 孤儿)→ fork 重建源取 messages + agent_id。"""
    src_sid = "src-orphan"
    # store 有 agent_id,内存 _sessions 无
    store.create(src_sid, "agent-os-v2", native_sid=src_sid, agent_id="french-expert")

    async def fake_build(sid, messages=None, agent_id=None):
        # 源重建调用:_load_native_messages 返 [] (store messages NULL)
        return _native_rec(messages=messages, spec_id=agent_id)
    monkeypatch.setattr(routes, "_build_native_session", fake_build)

    r = asyncio.run(routes.fork_session(
        "agent-os-v2", routes.ForkReq(source_session_id=src_sid, first_message="x"),
    ))
    assert r["forked"] is True
    # 源被重建回 _sessions
    assert routes._key("agent-os-v2", src_sid) in routes._sessions


# ── parent lineage(ADR-S2)────────────────────────────────────────────

def test_native_fork_sets_parent_session_id(store, monkeypatch):
    """fork 落库 parent_session_id == source(跨重启 lineage 可查)。"""
    src_sid = "src-parent"
    routes._sessions[routes._key("agent-os-v2", src_sid)] = _native_rec(spec_id="native")
    store.create(src_sid, "agent-os-v2", native_sid=src_sid, agent_id="native")
    monkeypatch.setattr(routes, "_build_native_session",
                        AsyncMock(side_effect=lambda sid, **kw: _native_rec(
                            messages=kw.get("messages"), spec_id=kw.get("agent_id") or "native")))

    r = asyncio.run(routes.fork_session(
        "agent-os-v2", routes.ForkReq(source_session_id=src_sid, first_message="x"),
    ))
    new_sid = r["forks"][0]["new_session_id"]
    row = store.get("agent-os-v2", new_sid)
    assert row["parent_session_id"] == src_sid


def test_parent_session_id_column_null_for_non_fork(store):
    """非 fork 创建的 session(parent_session_id)为 NULL(老行兼容)。"""
    store.create("plain", "agent-os-v2", native_sid="plain", agent_id="native")
    row = store.get("agent-os-v2", "plain")
    assert row["parent_session_id"] is None


def test_parent_session_id_migration_idempotent(tmp_path):
    """老 db(无 parent_session_id 列)→ 首次建 store ALTER 加列;二次开不重复 ALTER(幂等)。"""
    db = str(tmp_path / "old.db")
    # 模拟老 db:手动建无 parent_session_id 列的表
    import sqlite3
    conn = sqlite3.connect(db)
    conn.executescript("""CREATE TABLE orch_sessions (
        ext_id TEXT PRIMARY KEY, harness_type TEXT NOT NULL, native_sid TEXT,
        cwd TEXT, agent_id TEXT, created_at TEXT NOT NULL, last_turn_at TEXT,
        messages TEXT);""")
    conn.execute("INSERT INTO orch_sessions (ext_id, harness_type, native_sid, agent_id, created_at) "
                 "VALUES ('old1', 'agent-os-v2', 'old1', 'native', '2026-01-01')")
    conn.commit()
    conn.close()

    # 开 OrchSessionStore → _ensure_column migration
    s = OrchSessionStore(db)
    cols = {r["name"] for r in s._conn.execute("PRAGMA table_info(orch_sessions)").fetchall()}
    assert "parent_session_id" in cols
    # 老行 parent_session_id NULL(兼容)
    assert s.get("agent-os-v2", "old1")["parent_session_id"] is None
    s.close()

    # 二次开(幂等,不重复 ALTER)
    s2 = OrchSessionStore(db)
    cols2 = {r["name"] for r in s2._conn.execute("PRAGMA table_info(orch_sessions)").fetchall()}
    assert "parent_session_id" in cols2
    s2.close()


# ── fan-out(ADR-S3)───────────────────────────────────────────────────

def test_native_fork_fanout_n_branches(store, monkeypatch):
    """targets=[N branches] → N 新 session,各 parent_session_id == source,direction 各异。"""
    src_sid = "src-fanout"
    routes._sessions[routes._key("agent-os-v2", src_sid)] = _native_rec(spec_id="native")
    store.create(src_sid, "agent-os-v2", native_sid=src_sid, agent_id="native")
    monkeypatch.setattr(routes, "_build_native_session",
                        AsyncMock(side_effect=lambda sid, **kw: _native_rec(
                            messages=kw.get("messages"), spec_id=kw.get("agent_id") or "native")))

    targets = [routes.ForkTarget(first_message="dir A"),
               routes.ForkTarget(first_message="dir B"),
               routes.ForkTarget(first_message="dir C")]
    r = asyncio.run(routes.fork_session(
        "agent-os-v2",
        routes.ForkReq(source_session_id=src_sid, first_message="unused", targets=targets),
    ))
    assert len(r["forks"]) == 3
    sids = [f["new_session_id"] for f in r["forks"]]
    assert len(set(sids)) == 3  # 3 个不同新 session
    assert [f["direction"] for f in r["forks"]] == ["dir A", "dir B", "dir C"]
    for f in r["forks"]:
        assert f["source"] == src_sid
        assert f["forked"] is True
        assert store.get("agent-os-v2", f["new_session_id"])["parent_session_id"] == src_sid


def test_native_fork_empty_targets_falls_back_to_single_first_message(store, monkeypatch):
    """空 targets → 回退单 first_message(向后兼容)。"""
    src_sid = "src-fallback"
    routes._sessions[routes._key("agent-os-v2", src_sid)] = _native_rec(spec_id="native")
    store.create(src_sid, "agent-os-v2", native_sid=src_sid, agent_id="native")
    monkeypatch.setattr(routes, "_build_native_session",
                        AsyncMock(side_effect=lambda sid, **kw: _native_rec(
                            messages=kw.get("messages"), spec_id=kw.get("agent_id") or "native")))

    r = asyncio.run(routes.fork_session(
        "agent-os-v2",
        routes.ForkReq(source_session_id=src_sid, first_message="solo-direction"),
    ))
    assert len(r["forks"]) == 1
    assert r["forks"][0]["direction"] == "solo-direction"


def test_native_fork_clones_same_parent_messages_per_branch(store, monkeypatch):
    """fan-out 每 branch 都从同一父 messages 克隆(各自独立深拷贝,互不影响)。"""
    src_msgs = [ModelRequest(parts=[UserPromptPart(content="seed")])]
    src_sid = "src-perbranch"
    routes._sessions[routes._key("agent-os-v2", src_sid)] = _native_rec(
        messages=src_msgs, spec_id="native")
    store.create(src_sid, "agent-os-v2", native_sid=src_sid, agent_id="native")

    cloned_per_branch = {}

    async def fake_build(sid, messages=None, agent_id=None):
        cloned_per_branch[sid] = messages
        return _native_rec(messages=messages, spec_id=agent_id)
    monkeypatch.setattr(routes, "_build_native_session", fake_build)

    targets = [routes.ForkTarget(first_message="A"), routes.ForkTarget(first_message="B")]
    r = asyncio.run(routes.fork_session(
        "agent-os-v2", routes.ForkReq(source_session_id=src_sid, first_message="x", targets=targets),
    ))
    m1 = cloned_per_branch[r["forks"][0]["new_session_id"]]
    m2 = cloned_per_branch[r["forks"][1]["new_session_id"]]
    # 各自从源克隆,内容等
    assert m1 == src_msgs and m2 == src_msgs
    # 但彼此独立(非同一对象,改一支不污染另一支)
    assert m1 is not m2
    assert m1[0] is not m2[0]
    assert m1 is not src_msgs


# ── cc/claw 路径未破(ADR-S3:cc 保持 1:1,claw stub 501)────────────────

def test_cc_fork_path_unbroken(store, monkeypatch):
    """cc fork 仍走 client.fork(mock)→ 1:1 new_sid,不碰 fan-out。"""
    src_sid = "cc-src"
    fake_cc = MagicMock()
    fake_cc.fork = AsyncMock(return_value={"new_sid": "cc-new-uuid"})
    fake_cc.cwd = "/tmp"
    routes._sessions[routes._key("claude-code", src_sid)] = {
        "client": fake_cc, "session_id": src_sid, "harness_type": "claude-code",
        "agent_id": None, "native_sid": src_sid, "cwd": "/tmp",
    }
    store.create(src_sid, "claude-code", native_sid=src_sid, cwd="/tmp")
    monkeypatch.setattr(routes, "_create_claude", AsyncMock(return_value=MagicMock()))

    r = asyncio.run(routes.fork_session(
        "claude-code", routes.ForkReq(source_session_id=src_sid, first_message="hi"),
    ))
    assert r["new_session_id"] == "cc-new-uuid"
    assert r["source"] == src_sid
    assert r["forked"] is True
    fake_cc.fork.assert_awaited_once()


def test_claw_fork_still_501_stub(store, monkeypatch):
    """claw fork 仍是 ADR-4 stub(forked=False)→ 501,未碰。"""
    src_sid = "agent:main:main"
    fake_claw = MagicMock()
    fake_claw.fork = AsyncMock(return_value={"forked": False, "error": "not supported"})
    routes._sessions[routes._key("claw", src_sid)] = {
        "client": fake_claw, "session_id": src_sid, "harness_type": "claw",
        "agent_id": "main", "native_sid": src_sid, "cwd": None,
    }
    store.create(src_sid, "claw", native_sid=src_sid, agent_id="main")

    with pytest.raises(HTTPException) as ei:
        asyncio.run(routes.fork_session(
            "claw", routes.ForkReq(source_session_id=src_sid, first_message="hi"),
        ))
    assert ei.value.status_code == 501


# ── 404(源不存在)─────────────────────────────────────────────────────

def test_native_fork_source_not_found_404(store):
    """源 session store 无、内存无 → 404(非 None 静默)。"""
    with pytest.raises(HTTPException) as ei:
        asyncio.run(routes.fork_session(
            "agent-os-v2", routes.ForkReq(source_session_id="ghost", first_message="x"),
        ))
    assert ei.value.status_code == 404


# ── F3(ADR-S5):fork emit branch_created → observe parent 链 ──────────

def test_native_fork_emits_branch_created(store, monkeypatch):
    """F3:fork 成功时经 child emitter emit branch_created(parent_branch_id=source,
    branch_id=child, agent_id 透传)。fake emitter spy 断言 shape。"""
    src_sid = "src-emit"
    routes._sessions[routes._key("agent-os-v2", src_sid)] = _native_rec(spec_id="english-expert")
    store.create(src_sid, "agent-os-v2", native_sid=src_sid, agent_id="english-expert")
    monkeypatch.setattr(routes, "_build_native_session",
                        AsyncMock(side_effect=lambda sid, **kw: _native_rec(
                            messages=kw.get("messages"), spec_id=kw.get("agent_id") or "native")))

    r = asyncio.run(routes.fork_session(
        "agent-os-v2", routes.ForkReq(source_session_id=src_sid, first_message="branch"),
    ))
    new_sid = r["forks"][0]["new_session_id"]
    child_rec = routes._sessions[routes._key("agent-os-v2", new_sid)]
    # child emitter.emit 被调一次(branch_created)
    child_rec["emitter"].emit.assert_awaited_once()
    sent = child_rec["emitter"].emit.await_args.args[0]
    # shape 断言(ADR-S5)
    assert sent["event_type"] == "branch_created"
    assert sent["session_id"] == new_sid          # child(此事件属新 fork 分支)
    assert sent["data"]["parent_branch_id"] == src_sid  # source = parent
    assert sent["data"]["branch_id"] == new_sid   # child = branch
    assert sent["agent_id"] == "english-expert"   # 透传(ADR-1,D 的字段)
    assert sent["harness_type"] == "agent-os-v2"
    # 顶层 parent_session_id 键必须存在(observe from_dict 读顶层回填 session 行;
    # 漏写则 ws_ingest update_parent_session_id 永不触发 — skeptic 发现的回归点)
    assert sent["parent_session_id"] == src_sid


def test_native_fork_branch_created_agent_id_empty_when_no_spec(store, monkeypatch):
    """源 spec_id 为空 → branch_created agent_id=""(legacy 兼容,不崩)。"""
    src_sid = "src-nospec"
    routes._sessions[routes._key("agent-os-v2", src_sid)] = _native_rec(spec_id="")
    store.create(src_sid, "agent-os-v2", native_sid=src_sid, agent_id="")
    monkeypatch.setattr(routes, "_build_native_session",
                        AsyncMock(side_effect=lambda sid, **kw: _native_rec(
                            messages=kw.get("messages"), spec_id=kw.get("agent_id") or "")))

    r = asyncio.run(routes.fork_session(
        "agent-os-v2", routes.ForkReq(source_session_id=src_sid, first_message="x"),
    ))
    new_sid = r["forks"][0]["new_session_id"]
    child_rec = routes._sessions[routes._key("agent-os-v2", new_sid)]
    sent = child_rec["emitter"].emit.await_args.args[0]
    assert sent["agent_id"] == ""


def test_native_fork_emit_failure_does_not_break_fork(store, monkeypatch):
    """ADR-7:branch_created emit 抛异常不阻塞 fork(fire-and-forget,fork 仍成功返回)。"""
    src_sid = "src-emitfail"
    routes._sessions[routes._key("agent-os-v2", src_sid)] = _native_rec(spec_id="native")
    store.create(src_sid, "agent-os-v2", native_sid=src_sid, agent_id="native")

    async def fake_build(sid, messages=None, agent_id=None):
        rec = _native_rec(messages=messages, spec_id=agent_id)
        rec["emitter"] = AsyncMock()
        rec["emitter"].emit = AsyncMock(side_effect=RuntimeError("ws down"))
        return rec
    monkeypatch.setattr(routes, "_build_native_session", fake_build)

    r = asyncio.run(routes.fork_session(
        "agent-os-v2", routes.ForkReq(source_session_id=src_sid, first_message="x"),
    ))
    # emit 抛了但 fork 仍成功(不被污染)
    assert r["forked"] is True
    assert len(r["forks"]) == 1


def test_native_fork_no_emitter_skips_emit_safely(store, monkeypatch):
    """rec 无 emitter(边界)→ 不 emit,不崩(fork 仍成功)。"""
    src_sid = "src-noemit"
    routes._sessions[routes._key("agent-os-v2", src_sid)] = _native_rec(spec_id="native")
    store.create(src_sid, "agent-os-v2", native_sid=src_sid, agent_id="native")

    async def fake_build(sid, messages=None, agent_id=None):
        rec = _native_rec(messages=messages, spec_id=agent_id)
        rec["emitter"] = None  # 边界:无 emitter
        return rec
    monkeypatch.setattr(routes, "_build_native_session", fake_build)

    r = asyncio.run(routes.fork_session(
        "agent-os-v2", routes.ForkReq(source_session_id=src_sid, first_message="x"),
    ))
    assert r["forked"] is True


def test_branch_created_events_module_shape():
    """orchestrator events.branch_created 构造正确 dict(独立单测,不依赖 routes)。"""
    from src.harness.events import branch_created
    ev = branch_created(
        "agent-os-v2", "native_abc", "child-sid",
        branch_id="child-sid", parent_branch_id="parent-sid",
        agent_id="main",
    )
    assert ev["event_type"] == "branch_created"
    assert ev["session_id"] == "child-sid"
    assert ev["data"]["branch_id"] == "child-sid"
    assert ev["data"]["parent_branch_id"] == "parent-sid"
    assert ev["agent_id"] == "main"
    # 顶层 parent_session_id(observe from_dict 读此键回填 session 行)
    assert ev["parent_session_id"] == "parent-sid"
