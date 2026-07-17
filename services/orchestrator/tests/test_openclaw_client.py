"""L2: harness openclaw.py — v4 protocol frame mapping + handshake + dead-key detect.

WS v4 gateway I/O can't run headless, so we test the pure protocol/mapping
functions directly and drive the client methods with a fake WS / stubbed RPC:
frame serialization, foreign-session filtering (anti cross-session bleed),
ChatEvent/agent-tool → ObserveEvent mapping, the v4 connect handshake params
(minProtocol=maxProtocol=4, conditional auth.token, operator role), and
send_message dead-key detection (ok=false → stale + error tick closure).
"""

from __future__ import annotations

import json

import pytest

from src.harness.openclaw import (
    FRAME_REQ,
    PROTOCOL_VERSION,
    OpenClawClient,
    _foreign_session,
    map_agent_tool_event,
    map_chat_event,
    serialize_request_frame,
)


class _FakeEmitter:
    def __init__(self):
        self.emitted = []

    async def emit(self, ev):
        self.emitted.append(ev)

    async def connect(self):
        return True

    async def close(self):
        return None


class _FakeWS:
    def __init__(self, recv_payloads):
        self._recvs = list(recv_payloads)
        self.sent = []

    async def send(self, s):
        self.sent.append(s)

    async def recv(self):
        return self._recvs.pop(0)

    async def close(self):
        return None


# ── frame serialization ─────────────────────────────────────────────────

def test_serialize_request_frame_structure():
    d = json.loads(serialize_request_frame("r1", "connect", {"a": 1}))
    assert d == {"type": FRAME_REQ, "id": "r1", "method": "connect", "params": {"a": 1}}


def test_serialize_request_frame_default_empty_params():
    assert json.loads(serialize_request_frame("r2", "ping"))["params"] == {}


# ── foreign-session filter (anti cross-session bleed) ───────────────────

def test_foreign_session_own_false_foreign_and_missing_true():
    own = "agent:main:main"
    assert _foreign_session({"sessionKey": own}, own) is False
    assert _foreign_session({"sessionKey": "agent:other:x"}, own) is True
    assert _foreign_session({"session_key": own}, own) is False   # alt key spelling
    assert _foreign_session({}, own) is True                      # missing → defensive


# ── ChatEvent mapping ───────────────────────────────────────────────────

def test_map_chat_event_final_concatenates_text():
    ev = map_chat_event(
        {"state": "final", "runId": "run1", "sessionKey": "sk",
         "message": {"content": [{"type": "text", "text": "hello "},
                                 {"type": "text", "text": "world"}]}},
        "hid", "sk")
    assert ev["event_type"] == "tick_completed"
    assert ev["data"]["status"] == "success"
    assert ev["data"]["response"] == "hello world"
    assert ev["tick_id"] == "run1"


def test_map_chat_event_aborted_and_error_are_error_completions():
    ab = map_chat_event({"state": "aborted", "runId": "r"}, "h", "s")
    assert ab["data"]["status"] == "error"
    err = map_chat_event({"state": "error", "runId": "r",
                          "errorMessage": "boom", "errorKind": "X"}, "h", "s")
    assert err["data"]["status"] == "error"
    assert "X" in err["data"]["response"] and "boom" in err["data"]["response"]


def test_map_chat_event_delta_is_token_delta():
    ev = map_chat_event({"state": "delta", "runId": "r", "deltaText": "hi"}, "h", "s")
    assert ev["event_type"] == "token_delta"
    assert ev["data"]["delta_text"] == "hi"


def test_map_chat_event_unknown_state_returns_none():
    assert map_chat_event({"state": "weird"}, "h", "s") is None


# ── agent tool event mapping ────────────────────────────────────────────

def test_map_agent_tool_event_start_and_result():
    start = map_agent_tool_event(
        {"stream": "tool", "phase": "start", "runId": "r", "name": "search",
         "args": {"q": "x"}, "toolCallId": "c1"}, "h", "s")
    assert start["event_type"] == "tool_call"
    assert start["data"]["tool_name"] == "search"
    assert start["data"]["call_id"] == "c1"
    assert start["data"]["arguments"] == {"q": "x"}

    res = map_agent_tool_event(
        {"stream": "tool", "phase": "result", "runId": "r",
         "toolCallId": "c1", "result": "found"}, "h", "s")
    assert res["event_type"] == "tool_result"
    assert res["data"]["result"] == "found"

    assert map_agent_tool_event({"stream": "other"}, "h", "s") is None


# ── v4 connect handshake params ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_handle_challenge_v4_connect_params_with_auth():
    em = _FakeEmitter()
    client = OpenClawClient("agent:main:main", emitter=em)
    ws = _FakeWS([json.dumps({"type": "res", "id": "x", "ok": True,
                              "payload": {"protocol": 4,
                                          "auth": {"role": "operator"}}})])
    client.gateway_ws = ws

    await client._handle_challenge(
        {"type": "event", "event": "connect.challenge", "payload": {"nonce": "n123"}},
        "tok-abc")

    frame = json.loads(ws.sent[0])
    assert frame["method"] == "connect"
    p = frame["params"]
    assert p["minProtocol"] == PROTOCOL_VERSION == 4
    assert p["maxProtocol"] == 4
    assert p["role"] == "operator"
    assert p["scopes"] == ["operator.admin"]
    assert p["auth"] == {"token": "tok-abc"}
    assert p["client"]["id"] == OpenClawClient.CLIENT_ID


@pytest.mark.asyncio
async def test_handle_challenge_omits_auth_when_no_token():
    em = _FakeEmitter()
    client = OpenClawClient("sk", emitter=em)
    ws = _FakeWS([json.dumps({"type": "res", "id": "x", "ok": True,
                              "payload": {"protocol": 4}})])
    client.gateway_ws = ws
    await client._handle_challenge(
        {"type": "event", "event": "connect.challenge", "payload": {"nonce": "n"}},
        None)
    assert "auth" not in json.loads(ws.sent[0])["params"]


# ── send_message dead-key detection ────────────────────────────────────

@pytest.mark.asyncio
async def test_send_message_marks_stale_and_closes_tick_on_dead_key():
    em = _FakeEmitter()
    client = OpenClawClient("agent:dead:x", emitter=em)
    client.running = True
    client.gateway_ws = object()   # truthy: connected guard passes

    async def fake_request(method, params, timeout=15.0):
        return {"type": "res", "ok": False,
                "error": {"code": "NOT_FOUND", "message": "session not found"}}
    client._request = fake_request   # type: ignore[method-assign]

    await client.send_message("hi")

    assert client.stale is True
    assert [e["event_type"] for e in em.emitted] == ["tick_started", "tick_completed"]
    assert em.emitted[1]["data"]["status"] == "error"
    assert "session not found" in em.emitted[1]["data"]["response"]
