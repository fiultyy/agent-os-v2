"""L2: harness claude.py — stream-json parser, turn do_resume decision, delete path.

CLI PTY spawn can't run headless, so we test the pure logic around it:
StreamJSONParser line→event mapping, the turn() resume-vs-oneshot decision
(the ext→native 1:1 contract: native_sid missing forces oneshot even on
resume=True), delete() transcript path logic, and fork's no-fake-uuid guard.
_spawn_and_stream / subprocess are stubbed.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from src.harness.claude import HARNESS_TYPE, ClaudeClient, StreamJSONParser


class _FakeEmitter:
    def __init__(self):
        self.emitted = []

    async def emit(self, ev):
        self.emitted.append(ev)

    async def connect(self):
        return True

    async def close(self):
        return None


# ── StreamJSONParser ────────────────────────────────────────────────────

def test_parse_assistant_tool_use_emits_tool_call():
    p = StreamJSONParser("hid", "sid", tick_id="T1")
    line = json.dumps({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "Read", "id": "call_1",
         "input": {"file_path": "/x"}},
    ]}})
    ev = p.parse_line(line)
    assert ev["event_type"] == "tool_call"
    assert ev["harness_type"] == HARNESS_TYPE
    assert ev["tick_id"] == "T1"
    assert ev["data"]["tool_name"] == "Read"
    assert ev["data"]["call_id"] == "call_1"
    assert ev["data"]["arguments"] == {"file_path": "/x"}


def test_parse_user_tool_result_emits_tool_result():
    p = StreamJSONParser("hid", "sid", tick_id="T1")
    line = json.dumps({"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": "call_1", "content": "42",
         "is_error": False},
    ]}})
    ev = p.parse_line(line)
    assert ev["event_type"] == "tool_result"
    assert ev["data"]["call_id"] == "call_1"
    assert ev["data"]["result"] == "42"
    assert ev["data"]["error"] == ""


def test_parse_result_emits_tick_completed_and_captures_sid():
    p = StreamJSONParser("hid", "sid", tick_id="T1")
    line = json.dumps({"type": "result", "session_id": "native-uuid",
                       "result": "done", "is_error": False, "duration_ms": 123})
    ev = p.parse_line(line)
    assert ev["event_type"] == "tick_completed"
    assert ev["data"]["status"] == "success"
    assert ev["data"]["response"] == "done"
    assert p.captured_sid == "native-uuid"
    assert p.completed is True


def test_parse_result_is_error_maps_to_error_status():
    p = StreamJSONParser("hid", "sid")
    ev = p.parse_line(json.dumps({"type": "result", "is_error": True,
                                  "result": "boom"}))
    assert ev["data"]["status"] == "error"


def test_parse_non_json_unknown_type_and_text_block_return_none():
    p = StreamJSONParser("hid", "sid")
    assert p.parse_line("not json") is None
    assert p.parse_line(json.dumps({"type": "system"})) is None
    # assistant with only a text block (no tool_use) → nothing to emit
    assert p.parse_line(json.dumps({"type": "assistant", "message": {"content": [
        {"type": "text", "text": "hi"}]}})) is None


# ── ClaudeClient.turn resume-vs-oneshot decision ────────────────────────

def _stub_run_methods(client: ClaudeClient) -> list:
    """Replace _run_resume/_run_oneshot with recorders; return calls list."""
    calls: list = []

    async def fake_resume(parser, msg):
        calls.append(("resume", msg))

    async def fake_oneshot(parser, msg):
        calls.append(("oneshot", msg))

    client._run_resume = fake_resume       # type: ignore[method-assign]
    client._run_oneshot = fake_oneshot     # type: ignore[method-assign]
    return calls


@pytest.mark.asyncio
async def test_turn_resume_uses_resume_path_when_native_sid_present():
    client = ClaudeClient("ext-sid", emitter=_FakeEmitter())
    client.native_sid = "native-uuid"
    calls = _stub_run_methods(client)
    await client.turn("hi", resume=True)
    await asyncio.gather(*client._turn_tasks)
    assert calls == [("resume", "hi")]


@pytest.mark.asyncio
async def test_turn_forces_oneshot_when_native_sid_missing_even_if_resume():
    """ext→native 1:1 contract: native_sid None → oneshot (12-hex can't resume)."""
    client = ClaudeClient("ext-sid", emitter=_FakeEmitter())
    assert client.native_sid is None
    calls = _stub_run_methods(client)
    await client.turn("hi", resume=True)
    await asyncio.gather(*client._turn_tasks)
    assert calls == [("oneshot", "hi")]


@pytest.mark.asyncio
async def test_turn_oneshot_when_resume_false():
    client = ClaudeClient("ext-sid", emitter=_FakeEmitter())
    client.native_sid = "native-uuid"
    calls = _stub_run_methods(client)
    await client.turn("hi", resume=False)
    await asyncio.gather(*client._turn_tasks)
    assert calls == [("oneshot", "hi")]


@pytest.mark.asyncio
async def test_turn_emits_tick_started_and_returns_tick_id():
    em = _FakeEmitter()
    client = ClaudeClient("ext-sid", emitter=em)
    _stub_run_methods(client)
    res = await client.turn("hello", resume=False)
    await asyncio.gather(*client._turn_tasks)
    assert res["status"] == "started"
    assert res["tick_id"]
    assert em.emitted[0]["event_type"] == "tick_started"
    assert em.emitted[0]["data"]["request"] == "hello"


# ── ClaudeClient.delete transcript path ─────────────────────────────────

@pytest.mark.asyncio
async def test_delete_removes_native_sid_transcript(monkeypatch, tmp_path):
    em = _FakeEmitter()
    cwd = tmp_path / "proj"
    client = ClaudeClient("ext", cwd=cwd, emitter=em)
    client.native_sid = "native-uuid"

    slug = str(cwd).replace("/", "-")
    tdir = tmp_path / ".claude" / "projects" / slug
    tdir.mkdir(parents=True)
    transcript = tdir / "native-uuid.jsonl"
    transcript.write_text("[]")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    res = await client.delete()
    assert res == {"deleted": True, "sid": "native-uuid"}
    assert not transcript.exists()


@pytest.mark.asyncio
async def test_delete_already_gone_is_idempotent(monkeypatch, tmp_path):
    em = _FakeEmitter()
    client = ClaudeClient("ext", cwd=tmp_path / "proj", emitter=em)
    client.native_sid = "native-uuid"
    monkeypatch.setattr(Path, "home", lambda: tmp_path)   # transcript absent
    res = await client.delete()
    assert res["deleted"] is True


@pytest.mark.asyncio
async def test_delete_falls_back_to_ext_sid_when_no_native(monkeypatch, tmp_path):
    em = _FakeEmitter()
    client = ClaudeClient("ext-12hex", cwd=tmp_path / "proj", emitter=em)
    assert client.native_sid is None
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    res = await client.delete()
    assert res["sid"] == "ext-12hex"   # native None → sid fallback


# ── ClaudeClient.fork no-fake-uuid guard ────────────────────────────────

@pytest.mark.asyncio
async def test_fork_returns_none_new_sid_when_result_has_no_session_id():
    """captured_sid missing → new_sid=None (never fabricate; routes raises 500)."""
    em = _FakeEmitter()
    client = ClaudeClient("ext", emitter=em)

    async def fake_spawn(parser, cmd):
        return None   # subprocess stubbed; no result event captured
    client._spawn_and_stream = fake_spawn   # type: ignore[method-assign]

    res = await client.fork("orig-sid", "first msg")
    assert res["new_sid"] is None
    assert res["status"] == "failed"
