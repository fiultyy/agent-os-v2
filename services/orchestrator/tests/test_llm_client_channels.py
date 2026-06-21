"""Tests for P2 LLMClient dual-channel (OpenAI + Anthropic) + cache_control routing."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

try:
    import pysqlite3

    sys.modules["sqlite3"] = pysqlite3
    sys.modules["sqlite3.dbapi2"] = pysqlite3.dbapi2
except ImportError:
    pass

# .venv may lack httpx (llm_client's dep); inject a stub so the module
# imports. Tests mock httpx.AsyncClient per-test, so the stub only needs
# to exist + expose the exception types llm_client references.
try:
    import httpx  # noqa: F401
except ImportError:
    import types as _types

    _fake_httpx = _types.ModuleType("httpx")
    _fake_httpx.AsyncClient = object
    _fake_httpx.HTTPStatusError = type("HTTPStatusError", (Exception,), {})
    _fake_httpx.RequestError = type("RequestError", (Exception,), {})
    sys.modules["httpx"] = _fake_httpx

import pytest

from src.services.llm_client import LLMClient


class _FakeResp:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return self._payload


class _FakeClient:
    """Captures the request for assertions; returns a canned response."""

    def __init__(self, payload: dict, captured: dict, *args, **kwargs) -> None:
        self._payload = payload
        self._captured = captured

    async def __aenter__(self) -> "_FakeClient":
        return self

    async def __aexit__(self, *args) -> bool:
        return False

    async def post(self, url, headers=None, json=None):
        self._captured["url"] = url
        self._captured["headers"] = headers
        self._captured["json"] = json
        return _FakeResp(self._payload)


def _patch_httpx(monkeypatch, payload, captured):
    def factory(*args, **kwargs):
        return _FakeClient(payload, captured)

    monkeypatch.setattr("src.services.llm_client.httpx.AsyncClient", factory)


class TestChannelConfig:
    def test_openai_default(self, monkeypatch) -> None:
        monkeypatch.delenv("LLM_API_FORMAT", raising=False)
        c = LLMClient()
        assert c.format == "openai"

    def test_anthropic_format(self, monkeypatch) -> None:
        monkeypatch.setenv("LLM_API_FORMAT", "anthropic")
        monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tok")
        c = LLMClient()
        assert c.format == "anthropic"
        assert c.anthropic_api_key == "tok"


class TestOpenAIChannel:
    @pytest.mark.asyncio
    async def test_no_cache_control_in_payload(self, monkeypatch) -> None:
        monkeypatch.delenv("LLM_API_FORMAT", raising=False)
        monkeypatch.setenv("LLM_API_KEY", "k")
        captured: dict = {}
        _patch_httpx(
            monkeypatch,
            {"choices": [{"message": {"content": "ok"}}]},
            captured,
        )
        c = LLMClient()
        msgs = [{"role": "system", "content": "base"}, {"role": "user", "content": "hi"}]
        r = await c.chat(msgs, static_count=1)
        assert r == "ok"
        # OpenAI channel: messages sent verbatim, no cache_control anywhere
        assert "cache_control" not in str(captured["json"]["messages"])
        assert captured["url"].endswith("/chat/completions")


class TestAnthropicChannel:
    @pytest.mark.asyncio
    async def test_cache_control_injected_and_system_lifted(self, monkeypatch) -> None:
        monkeypatch.setenv("LLM_API_FORMAT", "anthropic")
        monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tok")
        captured: dict = {}
        _patch_httpx(
            monkeypatch,
            {"content": [{"type": "text", "text": "reply"}], "usage": {"cache_read_input_tokens": 10}},
            captured,
        )
        c = LLMClient()
        msgs = [
            {"role": "system", "content": "base"},
            {"role": "system", "content": "tools"},
            {"role": "user", "content": "hi"},
        ]
        r = await c.chat(msgs, static_count=2)
        assert r == "reply"
        assert c.last_usage == {"cache_read_input_tokens": 10}
        # Anthropic endpoint
        assert captured["url"].endswith("/v1/messages")
        assert captured["headers"]["x-api-key"] == "tok"
        assert captured["headers"]["anthropic-version"] == "2023-06-01"
        # system lifted to top-level with cache_control on static-end block
        system = captured["json"]["system"]
        assert isinstance(system, list)
        assert system[-1]["cache_control"] == {"type": "ephemeral"}
        # model is the anthropic model, not an OpenAI one
        assert captured["json"]["model"] == c.anthropic_model

    def test_to_anthropic_no_dynamic_system_demotion(self) -> None:
        """R2: compiler injects memory into the user tail, so all system
        messages are static — no [Memory context] demotion happens."""
        msgs = [
            {"role": "system", "content": "base"},      # static
            {"role": "system", "content": "tools"},     # static
            {"role": "user", "content": "q"},
        ]
        system, convo = LLMClient._to_anthropic(msgs, static_count=2)
        # both static system messages lifted to top-level system
        assert "base" in system and "tools" in system
        # no [Memory context] demotion message anywhere
        assert not any("[Memory context]" in str(m.get("content", "")) for m in convo)
        # user question preserved verbatim
        assert convo[-1] == {"role": "user", "content": "q"}

    def test_to_anthropic_raises_on_system_beyond_static(self) -> None:
        """R2: a system message beyond the static boundary means the
        compiler contract is broken — raise instead of silently demoting."""
        msgs = [
            {"role": "system", "content": "base"},      # static
            {"role": "system", "content": "tools"},     # static
            {"role": "system", "content": "stray"},     # beyond boundary
            {"role": "user", "content": "q"},
        ]
        with pytest.raises(ValueError):
            LLMClient._to_anthropic(msgs, static_count=2)

    def test_to_anthropic_static_count_zero_lifts_all_system(self) -> None:
        """When static_count == 0 (no cache hint), every system message
        lifts to top-level — no raise."""
        msgs = [
            {"role": "system", "content": "base"},
            {"role": "system", "content": "also-system"},
            {"role": "user", "content": "q"},
        ]
        system, convo = LLMClient._to_anthropic(msgs, static_count=0)
        assert "base" in system and "also-system" in system
        assert convo == [{"role": "user", "content": "q"}]

    def test_to_anthropic_preserves_cache_control_as_list(self) -> None:
        # when a static block carries cache_control, system stays a block list
        msgs = [
            {"role": "system", "content": [{"type": "text", "text": "base", "cache_control": {"type": "ephemeral"}}]},
        ]
        system, convo = LLMClient._to_anthropic(msgs, static_count=1)
        assert isinstance(system, list)
        assert system[0]["cache_control"] == {"type": "ephemeral"}
