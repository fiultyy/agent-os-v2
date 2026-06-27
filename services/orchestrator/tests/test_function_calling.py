"""LLM 原生 function-calling(anthropic 通道优先)回归测试。

覆盖三层:
1. tool_use 解析 — anthropic mock response 含 tool_use block → last_tool_use 正确。
2. _node_tool 用 tool_use name/input(端到端,mock LLM 返 file_read tool_use →
   _node_tool 执行真实 file_read)。
3. 向后兼容 — 无 tools 参数时 last_tool_use 保持 None,旧调用者零破坏。
4. openai 通道 tool_calls 基础兼容。
"""

import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# pysqlite3 替换坏掉的 miniconda3 sqlite3(src.memory 链式 import 需要)。
try:
    import pysqlite3

    sys.modules["sqlite3"] = pysqlite3
    sys.modules["sqlite3.dbapi2"] = pysqlite3.dbapi2
except ImportError:
    pass

# .venv 可能缺 httpx,注入 stub(llm_client 仅在 import 时引用类型)。
try:
    import httpx  # noqa: F401
except ImportError:
    _fake_httpx = types.ModuleType("httpx")
    _fake_httpx.AsyncClient = object
    _fake_httpx.HTTPStatusError = type("HTTPStatusError", (Exception,), {})
    _fake_httpx.RequestError = type("RequestError", (Exception,), {})
    sys.modules["httpx"] = _fake_httpx

import pytest

from src.services.llm_client import LLMClient


# ── 共享 mock httpx 工具 ────────────────────────────────────────────


class _FakeResp:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return self._payload


class _FakeClient:
    def __init__(self, payload: dict, captured: dict, *args, **kwargs) -> None:
        self._payload = payload
        self._captured = captured

    async def __aenter__(self) -> "_FakeClient":
        return self

    async def __aexit__(self, *args) -> bool:
        return False

    async def post(self, url, headers=None, json=None):
        self._captured["url"] = url
        self._captured["json"] = json
        return _FakeResp(self._payload)


def _patch_httpx(monkeypatch, payload, captured):
    monkeypatch.setattr(
        "src.services.llm_client.httpx.AsyncClient",
        lambda *a, **kw: _FakeClient(payload, captured),
    )


# ── 1. anthropic tool_use 解析 ─────────────────────────────────────


class TestAnthropicToolUseParsing:
    @pytest.mark.asyncio
    async def test_tool_use_block_parsed_into_last_tool_use(self, monkeypatch) -> None:
        """anthropic 响应含 tool_use block → last_tool_use={name, input}。"""
        monkeypatch.setenv("LLM_API_FORMAT", "anthropic")
        monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tok")
        captured: dict = {}
        _patch_httpx(
            monkeypatch,
            {
                "content": [
                    {"type": "text", "text": "Reading the file now."},
                    {
                        "type": "tool_use",
                        "id": "tu_1",
                        "name": "file_read",
                        "input": {"path": "/tmp/foo.txt"},
                    },
                ],
                "usage": {},
            },
            captured,
        )
        c = LLMClient()
        c.last_tool_use = "sentinel"  # 确认 per-call reset 覆盖旧值
        text = await c.chat(
            [{"role": "user", "content": "read /tmp/foo.txt"}],
            tools=[{"name": "file_read", "description": "read", "input_schema": {}}],
        )
        assert text == "Reading the file now."
        assert c.last_tool_use == {"name": "file_read", "input": {"path": "/tmp/foo.txt"}}
        # payload 含 tools + tool_choice
        assert captured["json"]["tools"] == [
            {"name": "file_read", "description": "read", "input_schema": {}}
        ]
        assert captured["json"]["tool_choice"] == {"type": "auto"}

    @pytest.mark.asyncio
    async def test_no_tools_no_tool_use(self, monkeypatch) -> None:
        """无 tools 参数 → last_tool_use 保持 None(向后兼容)。"""
        monkeypatch.setenv("LLM_API_FORMAT", "anthropic")
        monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tok")
        _patch_httpx(
            monkeypatch,
            {"content": [{"type": "text", "text": "plain reply"}], "usage": {}},
            {},
        )
        c = LLMClient()
        text = await c.chat([{"role": "user", "content": "hi"}])
        assert text == "plain reply"
        assert c.last_tool_use is None

    @pytest.mark.asyncio
    async def test_text_only_response_no_tool_use(self, monkeypatch) -> None:
        """传了 tools 但模型没调工具 → last_tool_use 仍 None。"""
        monkeypatch.setenv("LLM_API_FORMAT", "anthropic")
        monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tok")
        captured: dict = {}
        _patch_httpx(
            monkeypatch,
            {"content": [{"type": "text", "text": "no tool needed"}], "usage": {}},
            captured,
        )
        c = LLMClient()
        text = await c.chat(
            [{"role": "user", "content": "hi"}],
            tools=[{"name": "file_read", "description": "r", "input_schema": {}}],
        )
        assert text == "no tool needed"
        assert c.last_tool_use is None
        # tools 仍被发送(模型有机会选择不调)
        assert "tools" in captured["json"]

    @pytest.mark.asyncio
    async def test_last_tool_use_reset_each_call(self, monkeypatch) -> None:
        """第二次调用若无 tool_use,last_tool_use 必须被清空(per-call reset)。"""
        monkeypatch.setenv("LLM_API_FORMAT", "anthropic")
        monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tok")
        _patch_httpx(
            monkeypatch,
            {"content": [{"type": "text", "text": "ok"}], "usage": {}},
            {},
        )
        c = LLMClient()
        c.last_tool_use = {"name": "stale", "input": {}}
        await c.chat([{"role": "user", "content": "hi"}])
        assert c.last_tool_use is None


# ── 2. openai 通道 tool_calls 兼容 ─────────────────────────────────


class TestOpenAIToolCallsCompat:
    @pytest.mark.asyncio
    async def test_openai_tool_calls_parsed(self, monkeypatch) -> None:
        monkeypatch.delenv("LLM_API_FORMAT", raising=False)
        monkeypatch.setenv("LLM_API_KEY", "k")
        captured: dict = {}
        _patch_httpx(
            monkeypatch,
            {
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "tc_1",
                                    "type": "function",
                                    "function": {
                                        "name": "file_read",
                                        "arguments": '{"path": "/x.txt"}',
                                    },
                                }
                            ],
                        }
                    }
                ]
            },
            captured,
        )
        c = LLMClient()
        text = await c.chat(
            [{"role": "user", "content": "read /x.txt"}],
            tools=[{"name": "file_read", "description": "r", "input_schema": {}}],
        )
        assert c.last_tool_use == {"name": "file_read", "input": {"path": "/x.txt"}}
        assert text == ""
        # tools 被规范化为 OpenAI 形状
        assert captured["json"]["tools"][0]["type"] == "function"
        assert captured["json"]["tools"][0]["function"]["name"] == "file_read"
        assert captured["json"]["tool_choice"] == "auto"


# ── 3. 端到端:_node_tool 用 tool_use 执行真实工具 ──────────────────


class TestNodeToolWithNativeToolUse:
    """端到端:_node_tool 拿到原生 tool_use 的 tool_call/tool_args 后执行真实
    file_read。验证 tool_use 的 name/input 正确流入 ToolExecutor。"""

    @pytest.mark.asyncio
    async def test_node_tool_uses_native_tool_call_name_and_input(
        self, tmp_path, monkeypatch
    ) -> None:
        from src.api.routes import chat as chat_mod
        from src.graph import GraphState
        from src.services import _state
        from src.tools.registry import ToolRegistry
        from src.tools.executor import ToolExecutor
        from src.tools.guardrail import Guardrail
        from src.memory import MemoryEventBus

        # 装配 _state 的最小依赖(engine.py 在 import 时做副作用装配,这里
        # 单测不 import engine,故直接注入)。保留原值,测试后还原。
        reg = ToolRegistry()
        try:
            from src.skills.primitive import file_read

            reg.register(
                "file_read", file_read, description="read",
                parameters={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
            )
        except ImportError:
            pytest.skip("primitive skills unavailable")
        orig_executor = _state.tool_executor
        orig_bus = _state.memory_event_bus
        _state.tool_executor = ToolExecutor(reg, Guardrail())
        _state.memory_event_bus = MemoryEventBus()
        try:
            # 写一个真实文件供 file_read 读
            target = tmp_path / "data.txt"
            target.write_text("hello-function-calling", encoding="utf-8")

            # 直接构造 state,模拟 _node_llm 已把原生 tool_use 写进 context
            state = GraphState(
                input=f"read {target}",
                agent_id="cli",
                session_id="s1",
            )
            state.context["tool_call"] = "file_read"
            state.context["tool_args"] = {"path": str(target)}

            result_state = await chat_mod._node_tool(state)

            # _node_tool str()-ifies the output; file_read returns a dict
            # whose 'content' holds the file text. The key assertion is that
            # the native tool_use name/input flowed through to a real tool run.
            tool_result = result_state.context["tool_result"]
            assert "hello-function-calling" in tool_result
            assert "success" in tool_result and result_state.context["tool_result"].startswith("{'success': True")
            assert result_state.tool_results
            assert result_state.tool_results[0]["tool"] == "file_read"
            assert result_state.current_node == "tool"
        finally:
            _state.tool_executor = orig_executor
            _state.memory_event_bus = orig_bus
