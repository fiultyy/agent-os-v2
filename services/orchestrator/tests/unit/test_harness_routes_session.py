"""routes.py session 管理单测(方案 B+C):create 落库 / list 从 store / turn 自动
resume / delete 同步 / restore 分派重建。

mock ClaudeClient/OpenClawClient(避免真连 observe/gateway/spawn claude)。
native_sid 端到端回填由 curl 全链验证(需真跑 `claude -p` 拿 result 事件的 sid)。
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

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


# ── 巩固:再验修复的 bug 回归测 ────────────────────────────────────────

def test_ensure_client_lazy_restores_store_only(store, monkeypatch):
    # store 有但 _sessions 无(restore 失败的孤儿)→ _ensure_client 按需重建
    store.create("orphan", "claude-code", native_sid="uuid-o", cwd="/x")
    fake = _fake_cc(native_sid="uuid-o")
    monkeypatch.setattr(routes, "_create_claude", AsyncMock(return_value=fake))
    client = asyncio.run(routes._ensure_client("claude-code", "orphan"))
    assert client is fake
    assert routes._sessions[routes._key("claude-code", "orphan")]["native_sid"] == "uuid-o"


def test_ensure_client_returns_none_when_not_in_store(store):
    # store 也无 → None(调用方 404)
    assert asyncio.run(routes._ensure_client("claude-code", "ghost")) is None


def test_delete_store_only_orphan_cleans_store(store, monkeypatch):
    # store 有、_sessions 无 → delete 清 store + observe,不 404
    store.create("orphan2", "claude-code", native_sid="uuid-o2")
    monkeypatch.setattr(routes, "_observe_delete_session", AsyncMock(return_value=True))
    r = asyncio.run(routes.delete_session("claude-code", "orphan2"))
    assert r["status"] == "deleted"
    assert r["raw_deleted"] is False        # 无 client 可 stop
    assert store.get("claude-code", "orphan2") is None


def test_fork_no_captured_sid_returns_none_not_fake_uuid(monkeypatch):
    # fork 子进程无 result session_id → new_sid=None(绝不用伪 UUID 占位当 native)
    from src.harness.claude import ClaudeClient
    c = ClaudeClient("ext", native_sid="orig-uuid")
    c.emitter.emit = AsyncMock()  # type: ignore[method-assign]

    async def fake_spawn(parser, cmd):
        parser.captured_sid = None   # 模拟 stream 无 result session_id
    monkeypatch.setattr(c, "_spawn_and_stream", fake_spawn)
    r = asyncio.run(c.fork("orig-uuid", "first msg"))
    assert r["new_sid"] is None
    assert r["status"] == "failed"


# ── 巩固:死 key 检测(send_message 改 _request 等_res)─────────────────

def test_send_message_dead_key_marks_stale_and_emits_error_tick(monkeypatch):
    """send 死 key(gateway ok=false)→ stale=True + emit tick_started + error tick_completed。"""
    from src.harness.openclaw import OpenClawClient
    c = OpenClawClient(session_key="agent:main:dead")
    c.running = True
    c.gateway_ws = MagicMock()
    c.emitter = MagicMock()
    c.emitter.emit = AsyncMock()
    monkeypatch.setattr(c, "_request", AsyncMock(return_value={
        "ok": False, "error": {"code": "INVALID_REQUEST",
                                "message": "session not found: agent:main:dead"},
    }))
    asyncio.run(c.send_message("hi"))
    assert c.stale is True
    assert c.emitter.emit.call_count == 2          # tick_started + tick_completed(error)
    started = c.emitter.emit.call_args_list[0].args[0]
    completed = c.emitter.emit.call_args_list[1].args[0]
    assert started["event_type"] == "tick_started"
    assert started["data"]["request"] == "hi"
    assert completed["event_type"] == "tick_completed"
    assert completed["data"]["status"] == "error"


def test_send_message_ok_true_captures_run_id(monkeypatch):
    """send 活 key(ok=true)→ stale 仍 False,runId 从 res.payload 回填(供 tick 配对)。"""
    from src.harness.openclaw import OpenClawClient
    c = OpenClawClient(session_key="agent:main:main")
    c.running = True
    c.gateway_ws = MagicMock()
    c.emitter = MagicMock()
    c.emitter.emit = AsyncMock()
    monkeypatch.setattr(c, "_request", AsyncMock(return_value={
        "ok": True, "payload": {"runId": "run-abc-123"},
    }))
    asyncio.run(c.send_message("hello"))
    assert c.stale is False
    assert c._pending_run_id == "run-abc-123"


def test_reconnect_claw_revives_dead_client(store, monkeypatch):
    """claw 死 client(running=False)→ reconnect 清死重建,connected:true + _sessions 更新。"""
    dead = MagicMock(running=False)   # 模拟 WS 断后 running 永久 False
    alive = MagicMock(running=True)   # 重连后成功
    # 先在 store + _sessions 落一个死 client
    store.create("agent:main:main", "claw", native_sid="agent:main:main", agent_id="main")
    routes._sessions[routes._key("claw", "agent:main:main")] = {
        "client": dead, "session_id": "agent:main:main", "harness_type": "claw",
        "agent_id": "main", "native_sid": "agent:main:main", "cwd": None,
    }
    monkeypatch.setattr(routes, "_create_claw", AsyncMock(return_value=alive))

    r = asyncio.run(routes.reconnect_session("claw", "agent:main:main"))
    assert r["session_id"] == "agent:main:main"
    assert r["connected"] is True
    # 死 client 被替换
    rec = routes._sessions[routes._key("claw", "agent:main:main")]
    assert rec["client"] is alive
    assert rec["agent_id"] == "main"
    assert rec["native_sid"] == "agent:main:main"


def test_reconnect_claw_not_connected_returns_false(store, monkeypatch):
    """重连后仍 running=False(2s 内没起来)→ 200 + connected:false(不抛 500)。"""
    store.create("agent:main:dead", "claw", native_sid="agent:main:dead", agent_id="main")
    monkeypatch.setattr(routes, "_create_claw", AsyncMock(return_value=MagicMock(running=False)))
    # 跳过 2s 等待:sleep 立即返
    monkeypatch.setattr(routes.asyncio, "sleep", AsyncMock())
    r = asyncio.run(routes.reconnect_session("claw", "agent:main:dead"))
    assert r["connected"] is False


def test_trigger_turn_stale_client_auto_reconnects(store, monkeypatch):
    """trigger_turn 检测 stale client → 惰性重连(不直接 503)→ 重连后 send 成功。"""
    stale = MagicMock(running=True, stale=True)   # running 在但 stale(死键残留)
    alive = MagicMock(running=True, stale=False)  # 重连后的新 client
    alive.send_message = AsyncMock()
    store.create("agent:main:main", "claw", native_sid="agent:main:main", agent_id="main")
    routes._sessions[routes._key("claw", "agent:main:main")] = {
        "client": stale, "session_id": "agent:main:main", "harness_type": "claw",
        "agent_id": "main", "native_sid": "agent:main:main", "cwd": None,
    }
    monkeypatch.setattr(routes, "_reconnect_claw", AsyncMock(return_value=alive))
    req = routes.TurnReq(message="hi")
    r = asyncio.run(routes.trigger_turn("claw", "agent:main:main", req))
    assert r["status"] == "sent"
    alive.send_message.assert_awaited_once()  # 新 client 发了消息(非 stale 503)


def test_trigger_turn_reconnect_still_down_returns_503(store, monkeypatch):
    """重连后仍 running=False(gateway 真挂)→ 503(非 stale 卡死,是真连不上)。"""
    store.create("agent:main:dead", "claw", native_sid="agent:main:dead", agent_id="main")
    routes._sessions[routes._key("claw", "agent:main:dead")] = {
        "client": MagicMock(running=False, stale=True), "session_id": "agent:main:dead",
        "harness_type": "claw", "agent_id": "main", "native_sid": "agent:main:dead", "cwd": None,
    }
    monkeypatch.setattr(routes, "_reconnect_claw", AsyncMock(return_value=MagicMock(running=False)))
    from fastapi import HTTPException
    req = routes.TurnReq(message="hi")
    with pytest.raises(HTTPException) as e:
        asyncio.run(routes.trigger_turn("claw", "agent:main:dead", req))
    assert e.value.status_code == 503


def test_reconnect_claude_code_returns_400(store, monkeypatch):
    """cc 无状态,reconnect 无意义 → 400。"""
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        asyncio.run(routes.reconnect_session("claude-code", "any"))
    assert ei.value.status_code == 400


def test_reconnect_create_claw_failure_returns_404(store, monkeypatch):
    """_create_claw 异常(gateway 真不可达)→ _reconnect_claw None → reconnect 404。"""
    from fastapi import HTTPException

    async def boom(sid, agent):
        raise RuntimeError("gateway unreachable")
    monkeypatch.setattr(routes, "_create_claw", boom)
    with pytest.raises(HTTPException) as ei:
        asyncio.run(routes.reconnect_session("claw", "agent:main:main"))
    assert ei.value.status_code == 404


def test_reconnect_observe_only_session_lazy_creates(store, monkeypatch):
    """observe-only session(observe 有、或che store 无)→ reconnect 惰性建 + 落库 + connected。"""
    alive = MagicMock(running=True, stale=False)
    monkeypatch.setattr(routes, "_create_claw", AsyncMock(return_value=alive))
    r = asyncio.run(routes.reconnect_session("claw", "agent:english-expert:main"))
    assert r["connected"] is True
    row = routes._store.get("claw", "agent:english-expert:main")   # 落库(推断 agent)
    assert row is not None and row["agent_id"] == "english-expert"
    assert routes._key("claw", "agent:english-expert:main") in routes._sessions


def test_trigger_turn_stale_after_send_returns_503(store, monkeypatch):
    """重连后本次 send 又置 stale(send 真死 key)→ 503,调用方 delete+recreate。"""
    from fastapi import HTTPException
    fake = MagicMock(running=True, stale=True)   # 重连返回的 client,send 后仍 stale
    fake.send_message = AsyncMock()
    monkeypatch.setattr(routes, "_create_claw", AsyncMock(return_value=fake))
    sid = asyncio.run(
        routes.create_session("claw", routes.CreateSessionReq(agent_id="main"))
    )["session_id"]
    monkeypatch.setattr(routes, "_reconnect_claw", AsyncMock(return_value=fake))
    with pytest.raises(HTTPException) as ei:
        asyncio.run(routes.trigger_turn("claw", sid, routes.TurnReq(message="hi")))
    assert ei.value.status_code == 503
    fake.send_message.assert_awaited_once()       # 重连后 send 了(send 后才判 stale)


# ── P8:agent-os-v2 native routes(create/turn/delete)──────────────────

def _mock_native_rec(response: str = "ok") -> dict:
    """mock _build_native_session 返回:agent.run + emitter.close 受控。"""
    from pydantic_ai.messages import ModelResponse, TextPart
    agent = MagicMock()
    result = MagicMock()
    result.output = response
    # 真 ModelMessage(非 dict)——让 _persist_native_messages 序列化干净(defer5)
    result.all_messages = MagicMock(return_value=[
        ModelResponse(parts=[TextPart(content=response)])
    ])
    agent.run = AsyncMock(return_value=result)
    emitter = MagicMock()
    emitter.close = AsyncMock()
    return {
        "agent": agent, "emitter": emitter, "messages": [],
        "session_id": "s", "harness_type": "agent-os-v2", "native_sid": "s",
        # P1(T8):_build_native_session 返 dict 新增 cwd_scope/spec_id(create_session
        # 落库 agent_id + trigger_turn turn 前恢复 _active_cwd 依赖)。mock 给 default 值。
        "cwd_scope": [], "spec_id": "native",
    }


def test_create_native_session_persists(store, monkeypatch):
    rec = _mock_native_rec()
    monkeypatch.setattr(routes, "_build_native_session", AsyncMock(return_value=rec))
    res = asyncio.run(routes.create_session("agent-os-v2", routes.CreateSessionReq()))
    assert res["status"] == "created"
    assert res["type"] == "agent-os-v2"
    sid = res["session_id"]
    assert store.get("agent-os-v2", sid)["native_sid"] == sid


def test_trigger_turn_native_runs_agent_and_updates_messages(store, monkeypatch):
    rec = _mock_native_rec("hello back")
    monkeypatch.setattr(routes, "_build_native_session", AsyncMock(return_value=rec))
    sid = asyncio.run(routes.create_session("agent-os-v2", routes.CreateSessionReq()))["session_id"]
    res = asyncio.run(routes.trigger_turn("agent-os-v2", sid, routes.TurnReq(message="hi")))
    assert res["status"] == "completed"
    assert res["response"] == "hello back"
    rec["agent"].run.assert_awaited_once()
    # message_history 续聊:all_messages() 回写真 ModelResponse
    from pydantic_ai.messages import ModelResponse
    assert len(rec["messages"]) == 1 and isinstance(rec["messages"][0], ModelResponse)


def test_trigger_turn_native_store_only_orphan_rebuilds(store, monkeypatch):
    """store 有、_sessions 无(restore 孤儿)→ trigger_turn 重建 native agent(不 404)。"""
    store.create("orphan-native", "agent-os-v2", native_sid="orphan-native")
    rec = _mock_native_rec("rebuilt")
    monkeypatch.setattr(routes, "_build_native_session", AsyncMock(return_value=rec))
    res = asyncio.run(routes.trigger_turn("agent-os-v2", "orphan-native", routes.TurnReq(message="hi")))
    assert res["status"] == "completed"


def test_trigger_turn_native_not_found_404(store):
    with pytest.raises(HTTPException) as ei:
        asyncio.run(routes.trigger_turn("agent-os-v2", "ghost", routes.TurnReq(message="hi")))
    assert ei.value.status_code == 404


def test_delete_native_session_closes_emitter(store, monkeypatch):
    rec = _mock_native_rec()
    monkeypatch.setattr(routes, "_build_native_session", AsyncMock(return_value=rec))
    monkeypatch.setattr(routes, "_observe_delete_session", AsyncMock(return_value=True))
    sid = asyncio.run(routes.create_session("agent-os-v2", routes.CreateSessionReq()))["session_id"]
    res = asyncio.run(routes.delete_session("agent-os-v2", sid))
    assert res["status"] == "deleted"
    rec["emitter"].close.assert_awaited()
    assert store.get("agent-os-v2", sid) is None


def test_spawn_native_returns_400(store, monkeypatch):
    monkeypatch.setattr(routes, "_build_native_session", AsyncMock(return_value=_mock_native_rec()))
    sid = asyncio.run(routes.create_session("agent-os-v2", routes.CreateSessionReq()))["session_id"]
    with pytest.raises(HTTPException) as ei:
        asyncio.run(routes.spawn_instance("agent-os-v2", sid))
    assert ei.value.status_code == 400  # in-process 无 spawn


def test_restore_native_session_rebuilds(store, monkeypatch):
    """restore_all_sessions 加 agent-os-v2 分支:store 有 → 重建 native agent。"""
    store.create("nat1", "agent-os-v2", native_sid="nat1")
    rec = _mock_native_rec()
    monkeypatch.setattr(routes, "_build_native_session", AsyncMock(return_value=rec))
    monkeypatch.setattr(routes, "_sessions", {})
    restored = asyncio.run(routes.restore_all_sessions())
    assert restored["agent-os-v2"] == 1
    assert routes._sessions[routes._key("agent-os-v2", "nat1")] is rec


# ── native message_history 持久化 helper(defer5)──────────────────────

def test_persist_load_native_messages_roundtrip(store):
    """真 ModelMessage persist→load round-trip(续聊重启可重建)。"""
    from pydantic_ai.messages import ModelRequest, UserPromptPart
    store.create("s1", "agent-os-v2", native_sid="s1")
    msgs = [ModelRequest(parts=[UserPromptPart(content="你好")])]
    routes._persist_native_messages("s1", msgs)
    loaded = routes._load_native_messages("s1")
    assert len(loaded) == 1
    assert isinstance(loaded[0], ModelRequest)
    assert loaded[0].parts[0].content == "你好"


def test_load_native_messages_bad_json_returns_empty(store):
    """坏 JSON(老格式/损坏)→ [],降级 fresh start 不破续聊。"""
    store.create("s2", "agent-os-v2", native_sid="s2")
    store.save_messages("s2", "{not valid json")
    assert routes._load_native_messages("s2") == []


def test_load_native_messages_empty_when_unwritten(store):
    store.create("s3", "agent-os-v2", native_sid="s3")
    assert routes._load_native_messages("s3") == []


# ── MemoryCapability 注入(defer4)─────────────────────────────────────

def test_get_memory_tools_none_when_no_kg(monkeypatch):
    """env gate:_state.knowledge_graph None → 不注入 MemoryCapability(ADR-7)。"""
    import src.services._state as _st
    monkeypatch.setattr(routes, "_memory_tools", None)
    monkeypatch.setattr(_st, "knowledge_graph", None)
    assert routes._get_memory_tools() is None


def test_get_memory_tools_constructs_and_caches(monkeypatch):
    """knowledge_graph 通电 → 构造 ExperienceTool+KGMemoryTool 单例,二次调缓存同对象
    (避免 per-session ThreadPoolExecutor 泄漏)。"""
    import src.memory.experience_kg as ekg_mod
    import src.memory.tools.experience_tool as et_mod
    import src.memory.tools.kg_memory_tool as kmt_mod
    import src.memory.kg_query_interface as kqi_mod
    import src.services._state as _st

    monkeypatch.setattr(routes, "_memory_tools", None)
    monkeypatch.setattr(_st, "knowledge_graph", MagicMock())  # 通电
    exp_fake, kg_fake = MagicMock(), MagicMock()
    monkeypatch.setattr(ekg_mod, "ExperienceKG", MagicMock())
    monkeypatch.setattr(kqi_mod, "KGQueryInterface", MagicMock())
    monkeypatch.setattr(et_mod, "ExperienceTool", MagicMock(return_value=exp_fake))
    monkeypatch.setattr(kmt_mod, "KGMemoryTool", MagicMock(return_value=kg_fake))

    mt1 = routes._get_memory_tools()
    assert mt1 == (exp_fake, kg_fake)
    assert routes._get_memory_tools() is mt1   # 缓存:二次调同对象


# ── ADR-1/ADR-2: native session build 注入 profile caps + R2 cache model_settings ──

def test_build_native_session_model_settings_has_cache_fields(monkeypatch):
    """ADR-2:_build_native_session 真跑 → agent.model_settings 含两 cache 字段="5m"
    + profile caps 注入可见(ADR-1)。mock emitter/skill/gateway 避免真连。"""
    import src.services._state as _st
    from src.agent.profile import AgentBaseProfile, LayerProfile
    from src.harness.capabilities import LayerCapability

    # mock profile_registry 注入测试 profile(L1 + L2)
    fake_profile = AgentBaseProfile(agent_id="native")
    fake_profile.add_layer(LayerProfile(layer=1, source="id_test", content="ID-L1-MARKER"))
    fake_profile.add_layer(LayerProfile(layer=2, source="rules_test", content="RULES-L2-MARKER"))
    fake_registry = MagicMock()
    fake_registry.get.return_value = fake_profile
    monkeypatch.setattr(_st, "profile_registry", fake_registry)

    # mock emitter 连接(避免真连 observe)
    async def _noop(*a, **kw):
        return None
    from src.harness import routes as r
    monkeypatch.setattr(r, "_get_memory_tools", lambda: None)  # 跳过 MemoryCapability(免 KG)
    monkeypatch.setattr("src.harness.emit.ObserveEmitter.connect", _noop)

    # 真 _build_native_session(不 mock 它本身),拿回 agent
    rec = asyncio.run(r._build_native_session("sid-test", messages=[]))
    agent = rec["agent"]
    # ADR-2: model_settings 透传两 cache 字段
    ms = agent.model_settings
    assert ms.get("anthropic_cache_instructions") == "5m", f"instructions cache 缺: {ms}"
    assert ms.get("anthropic_cache_tool_definitions") == "5m", f"tool cache 缺: {ms}"
    # ADR-1: profile caps 注入可见(LayerCapability 经 get_instructions 进 system prompt)
    blob = "\n".join(agent._cap_instructions)
    assert "ID-L1-MARKER" in blob
    assert "RULES-L2-MARKER" in blob


# ── native usage → observe(TUI 状态栏 token/模型)─────────────────────

def test_emit_native_usage_emits_usage_event():
    """_emit_native_usage 把 result.usage + model 包成 usage event → observe。"""
    import asyncio
    from src.harness.routes import _emit_native_usage

    emitted = []
    class FakeEmitter:
        async def emit(self, event):
            emitted.append(event)

    class FakeUsage:
        input_tokens = 100
        output_tokens = 50
        cache_read_tokens = 200

    asyncio.run(_emit_native_usage(FakeEmitter(), "abc123def456", FakeUsage()))
    assert len(emitted) == 1
    ev = emitted[0]
    assert ev["event_type"] == "usage"
    assert ev["harness_type"] == "agent-os-v2"
    assert ev["harness_id"] == "native_abc123de"  # session_id[:8]
    assert ev["data"]["input"] == 100
    assert ev["data"]["output"] == 50
    assert ev["data"]["cache_read"] == 200
    assert ev["data"]["model"]  # 模型名非空


def test_emit_native_usage_none_emitter_noop():
    """emitter=None → 不 emit(best-effort,无 session 场景安全)。"""
    import asyncio
    from src.harness.routes import _emit_native_usage
    # usage=None + emitter=None:getattr 兜底 + 早返,不崩
    asyncio.run(_emit_native_usage(None, "s1", None))


# ── P1 T8:per-agent spec / cwd_scope / MultiCwdScopeCapability 注入 / active_cwd ──

def _wire_lightweight_native(monkeypatch, agent_registry=None):
    """禁重依赖(emitter 连接 / skill 扫描 / mcp / memory / tool_executor),让
    _build_native_session 真 build 一 Agent 验 caps/cwd_scope 注入。"""
    import src.services._state as _st
    from src.harness import routes as r

    async def _noop(*a, **kw):
        return None
    monkeypatch.setattr("src.harness.emit.ObserveEmitter.connect", _noop)
    monkeypatch.setattr(r, "_get_memory_tools", lambda: None)
    # skill 扫描返空(避免读真 SKILL.md + 让 spec.skills 过滤测不被全量污染)
    monkeypatch.setattr("src.harness.capabilities.skill_capability.make_skill_capabilities",
                        lambda loader: [])
    monkeypatch.setattr("src.harness.routes.load_global_mcp_servers",
                        lambda: None, raising=False)
    # ToolBridge executor/pitfail None-safe(返空 toolset,no-op)
    monkeypatch.setattr(_st, "tool_executor", None)
    monkeypatch.setattr(_st, "pitfail_registry", None)
    monkeypatch.setattr(_st, "memory_event_bus", None)
    monkeypatch.setattr(_st, "knowledge_graph", None)
    monkeypatch.setattr(_st, "profile_registry", None)
    monkeypatch.setattr(_st, "agent_registry", agent_registry)


def test_build_native_session_per_agent_cwd_scope_and_capability(monkeypatch, tmp_path):
    """两 agent(native 多 cwd + 另一单 cwd)→ _build_native_session 返的 cwd_scope
    对应各自 spec;agent caps 含 MultiCwdScopeCapability 且指令含各自 cwd 清单。"""
    import asyncio
    from src.agent.agent_registry import AgentRegistry
    from src.agent.agent_spec import AgentSpec, CwdEntry
    from src.harness import routes as r

    repo = tmp_path / "repo"
    repo.mkdir()
    spec_native = AgentSpec(
        id="native", default=True,
        cwds=[
            CwdEntry(path="services/orchestrator", label="orch", default=True),
            CwdEntry(path="apps/tui-rs", label="tui"),
        ],
    )
    spec_single = AgentSpec(id="other", cwds=[CwdEntry(path="apps/x", label="x", default=True)])
    reg = AgentRegistry()
    reg._agents = {"native": spec_native, "other": spec_single}
    reg._default_id = "native"
    monkeypatch.setenv("AO2_REPO_ROOT", str(repo))
    _wire_lightweight_native(monkeypatch, agent_registry=reg)

    rec_native = asyncio.run(r._build_native_session("snat", agent_id="native"))
    rec_other = asyncio.run(r._build_native_session("soth", agent_id="other"))

    # cwd_scope 长度 + label 对应各自 spec
    labels_native = [e.label for e in rec_native["cwd_scope"]]
    labels_other = [e.label for e in rec_other["cwd_scope"]]
    assert labels_native == ["orch", "tui"], labels_native
    assert labels_other == ["x"], labels_other
    assert rec_native["spec_id"] == "native"
    assert rec_other["spec_id"] == "other"

    # agent caps 含 MultiCwdScopeCapability,指令含各自 cwd 清单(绝对路径)
    instr_native = "\n".join(rec_native["agent"]._cap_instructions)
    instr_other = "\n".join(rec_other["agent"]._cap_instructions)
    assert "Multi-CWD Scope" in instr_native
    assert str((repo / "services/orchestrator").resolve()) in instr_native
    assert str((repo / "apps/tui-rs").resolve()) in instr_native
    assert str((repo / "apps/x").resolve()) in instr_other
    assert str((repo / "services/orchestrator").resolve()) not in instr_other


def test_build_native_session_skills_filtered_by_spec(monkeypatch, tmp_path):
    """spec.skills 非空 → 只保留匹配的 SkillCapability;空 → 全保留(向后兼容)。"""
    import asyncio
    from src.agent.agent_registry import AgentRegistry
    from src.agent.agent_spec import AgentSpec
    from src.harness import routes as r
    from src.harness.capabilities.skill_capability import SkillCapability

    repo = tmp_path / "repo"; repo.mkdir()
    monkeypatch.setenv("AO2_REPO_ROOT", str(repo))
    spec = AgentSpec(id="native", default=True, cwds=[], skills=["ao2-architecture", "absent-skill"])

    # 真 make_skill_capabilities 产 3 个(含 ao2-architecture + 两个无关)
    def fake_make(loader):
        return [
            SkillCapability(id="ao2-architecture", skill=None),
            SkillCapability(id="other-a", skill=None),
            SkillCapability(id="other-b", skill=None),
        ]
    monkeypatch.setattr("src.harness.capabilities.skill_capability.make_skill_capabilities",
                        fake_make)
    _wire_lightweight_native(monkeypatch, agent_registry=None)
    # 手装 registry(skill 过滤分支走 spec.skills)
    import src.services._state as _st
    reg = AgentRegistry(); reg._agents = {"native": spec}; reg._default_id = "native"
    monkeypatch.setattr(_st, "agent_registry", reg)

    # capture caps passed to build_native_agent(Agent 不暴露 capabilities list)
    captured_caps = {}
    import src.harness.routes as _rr
    real_build = _rr.build_native_agent if hasattr(_rr, "build_native_agent") else None

    def spy_build(*a, **kw):
        captured_caps["caps"] = list(kw.get("capabilities") or [])
        from unittest.mock import MagicMock
        ag = MagicMock()
        ag._cap_instructions = []
        ag.model_settings = kw.get("model_settings") or {}
        return ag
    # build_native_agent 在 _build_native_session 内惰性 import,patch 模块属性
    import src.harness.native_agent as _na
    monkeypatch.setattr(_na, "build_native_agent", spy_build)

    rec = asyncio.run(r._build_native_session("sfilt", agent_id="native"))
    skill_ids = sorted(c.id for c in captured_caps.get("caps", [])
                       if isinstance(c, SkillCapability))
    # spec.skills=[ao2-architecture, absent-skill] → 只保留命中的 ao2-architecture
    assert skill_ids == ["ao2-architecture"], skill_ids


def test_build_native_session_wires_spec_instructions(monkeypatch, tmp_path):
    """ADR-1:AgentSpec.instructions dead 字段接线 → build_native_agent 收到 instructions=。

    两分支:
      - 非空 spec.instructions → 透传(spec.instructions 原样进 kw)
      - None spec.instructions → 兜底 ""(维持 default 旧行为,不 crash)
    """
    import asyncio
    from src.agent.agent_registry import AgentRegistry
    from src.agent.agent_spec import AgentSpec
    from src.harness import routes as r

    repo = tmp_path / "repo"; repo.mkdir()
    monkeypatch.setenv("AO2_REPO_ROOT", str(repo))

    captured = {}

    def spy_build(*a, **kw):
        captured["instructions"] = kw.get("instructions")
        from unittest.mock import MagicMock
        ag = MagicMock()
        ag._cap_instructions = []
        ag.model_settings = kw.get("model_settings") or {}
        return ag
    import src.harness.native_agent as _na
    monkeypatch.setattr(_na, "build_native_agent", spy_build)

    # 分支 1:非空 instructions 透传
    spec_nonempty = AgentSpec(id="native", default=True, cwds=[],
                              instructions="You are a help agent.")
    from src.agent.agent_registry import AgentRegistry as _AR
    reg = _AR(); reg._agents = {"native": spec_nonempty}; reg._default_id = "native"
    _wire_lightweight_native(monkeypatch, agent_registry=reg)
    asyncio.run(r._build_native_session("sinstr", agent_id="native"))
    assert captured["instructions"] == "You are a help agent.", captured["instructions"]

    # 分支 2:None instructions → "" 兜底(default 旧行为,不 crash)
    spec_none = AgentSpec(id="native", default=True, cwds=[], instructions=None)
    reg2 = _AR(); reg2._agents = {"native": spec_none}; reg2._default_id = "native"
    import src.services._state as _st
    monkeypatch.setattr(_st, "agent_registry", reg2)
    captured.clear()
    asyncio.run(r._build_native_session("sempty", agent_id="native"))
    assert captured["instructions"] == "", captured["instructions"]



def test_resolve_relative_uses_active_cwd_absolute_passthrough(monkeypatch, tmp_path):
    """set_active_cwd(label) 后相对路径 _resolve 走新 cwd;绝对路径直通不变。"""
    from pathlib import Path
    from src.tools.cwd_scope import _resolve, _active_cwd, set_session_active_cwd, _SESSION_CWD

    cwd_a = tmp_path / "a"; cwd_a.mkdir()
    cwd_b = tmp_path / "b"; cwd_b.mkdir()
    (cwd_a / "rel.txt").write_text("FROM-A")
    (cwd_b / "rel.txt").write_text("FROM-B")
    abs_file = cwd_a / "abs.txt"; abs_file.write_text("ABS")

    # 清 session 持久 dict 避免跨测污染
    _SESSION_CWD.pop("test:rel", None)
    # 激活 cwd_a → 相对 rel.txt 读到 FROM-A
    _active_cwd.set(Path(str(cwd_a)))
    assert _resolve("rel.txt").read_text() == "FROM-A"
    # 切到 cwd_b → 相对 rel.txt 读到 FROM-B
    set_session_active_cwd("test:rel", str(cwd_b))
    assert _resolve("rel.txt").read_text() == "FROM-B"
    # 绝对路径直通(不受 active_cwd 影响)
    assert _resolve(str(abs_file)).read_text() == "ABS"
    _SESSION_CWD.pop("test:rel", None)


def test_create_native_session_persists_agent_id(monkeypatch, tmp_path):
    """create_session 传 agent_id → _store 落库 agent_id 列 == normalize_agent_id(spec.id)。"""
    import asyncio
    from src.agent.agent_registry import AgentRegistry
    from src.agent.agent_spec import AgentSpec, CwdEntry
    from src.harness import routes as r
    from src.harness.session_store import OrchSessionStore

    repo = tmp_path / "repo"; repo.mkdir()
    spec = AgentSpec(id="other-agent", cwds=[CwdEntry(path="x", label="x", default=True)])
    reg = AgentRegistry(); reg._agents = {"other-agent": spec}; reg._default_id = "other-agent"
    monkeypatch.setenv("AO2_REPO_ROOT", str(repo))
    store = OrchSessionStore(str(tmp_path / "t.db"))
    monkeypatch.setattr(r, "_store", store)
    monkeypatch.setattr(r, "_sessions", {})

    # 真 _build_native_session(mock 重依赖)
    _wire_lightweight_native(monkeypatch, agent_registry=reg)

    res = asyncio.run(r.create_session("agent-os-v2", r.CreateSessionReq(agent_id="other-agent")))
    sid = res["session_id"]
    row = store.get("agent-os-v2", sid)
    assert row is not None
    assert row["agent_id"] == "other-agent"  # normalize_agent_id 幂等


def test_trigger_turn_restore_rebuilds_same_spec_session(monkeypatch, tmp_path):
    """store 有 agent_id、_sessions 无(restore 孤儿)→ trigger_turn 从 store 取 agent_id
    重建同 spec session(续聊保留 per-agent cwd_scope + active_cwd)。"""
    import asyncio
    from pathlib import Path
    from src.agent.agent_registry import AgentRegistry
    from src.agent.agent_spec import AgentSpec, CwdEntry
    from src.harness import routes as r
    from src.harness.session_store import OrchSessionStore
    from src.tools.cwd_scope import _SESSION_CWD

    repo = tmp_path / "repo"; repo.mkdir()
    spec = AgentSpec(id="native", default=True,
                     cwds=[CwdEntry(path="svc/o", label="orch", default=True)])
    reg = AgentRegistry(); reg._agents = {"native": spec}; reg._default_id = "native"
    monkeypatch.setenv("AO2_REPO_ROOT", str(repo))
    store = OrchSessionStore(str(tmp_path / "t2.db"))
    monkeypatch.setattr(r, "_store", store)
    monkeypatch.setattr(r, "_sessions", {})
    _wire_lightweight_native(monkeypatch, agent_registry=reg)

    # store 有 agent-os-v2 记录(含 agent_id),内存无 → 走 restore 分支
    store.create("restore-sid", "agent-os-v2", native_sid="restore-sid", agent_id="native")
    # agent.run mock 成(避免真 LLM 调用)
    captured = {}

    async def fake_run(msg, message_history=None):
        captured["msg"] = msg
        from pydantic_ai.messages import ModelResponse, TextPart
        from unittest.mock import MagicMock
        result = MagicMock()
        result.output = "rebuilt-ok"
        result.all_messages = MagicMock(return_value=[ModelResponse(parts=[TextPart(content="rebuilt-ok")])])
        result.usage = MagicMock(input_tokens=0, output_tokens=0, cache_read_tokens=0)
        return result

    rec = asyncio.run(r._build_native_session("restore-sid", agent_id="native"))
    rec["agent"].run = fake_run
    monkeypatch.setattr(r, "_build_native_session", AsyncMock(return_value=rec))

    res = asyncio.run(r.trigger_turn("agent-os-v2", "restore-sid", r.TurnReq(message="hi")))
    assert res["status"] == "completed"
    assert res["response"] == "rebuilt-ok"
    # restore 用了 store 的 agent_id(native)重建 → cwd_scope 对应 spec
    rebuilt = r._sessions[r._key("agent-os-v2", "restore-sid")]
    assert [e.label for e in rebuilt["cwd_scope"]] == ["orch"]
    assert rebuilt["spec_id"] == "native"
    _SESSION_CWD.pop("agent-os-v2:restore-sid", None)


def test_list_sessions_merges_session_active_cwd(store):
    """ADR-3:agent-os-v2 session store cwd=None(创建即 None),turn 时 _active_cwd 落
    _SESSION_CWD。GET sessions 应按 session_key 查 _SESSION_CWD 合并,使 cwd 非 None。"""
    # agent-os-v2 session:store 创建即 cwd=None(native agent 无 req.cwd)
    from src.tools.cwd_scope import _SESSION_CWD
    store.create("v2-sid", "agent-os-v2", native_sid="v2-native", agent_id="native")
    assert store.get("agent-os-v2", "v2-sid")["cwd"] is None   # 前置:确为 None
    # turn 时记录了 session 级 active cwd(实际由 cwd_scope_capability / turn 写入)
    _SESSION_CWD["agent-os-v2:v2-sid"] = "/abs/workspace"
    try:
        out = asyncio.run(routes.list_sessions("agent-os-v2"))
        s = next(x for x in out["sessions"] if x["session_id"] == "v2-sid")
        assert s["cwd"] == "/abs/workspace"   # 合并后非 None
    finally:
        _SESSION_CWD.pop("agent-os-v2:v2-sid", None)


def test_list_sessions_store_cwd_wins(store):
    """ADR-3:store cwd 优先,仅 None 时 fallback _SESSION_CWD(claw/claude 不回归)。"""
    from src.tools.cwd_scope import _SESSION_CWD
    store.create("cc-sid", "claude-code", native_sid="uuid-cc", cwd="/store/cwd")
    _SESSION_CWD["claude-code:cc-sid"] = "/should-not-win"
    try:
        out = asyncio.run(routes.list_sessions("claude-code"))
        s = next(x for x in out["sessions"] if x["session_id"] == "cc-sid")
        assert s["cwd"] == "/store/cwd"       # store cwd 优先
    finally:
        _SESSION_CWD.pop("claude-code:cc-sid", None)

