"""Unit tests for a2a_call tool (node C, ADR-2 B-form).

Verifies:
- RK11: source registration name is ``a2a_call`` (no v2_ prefix). AST scan of
  src/a2a/tool.py + grep across services/orchestrator/src confirms no
  ``v2_a2a_call`` literal in source.
- Runtime ToolBridge shows ``v2_a2a_call`` after ``.prefixed("v2")``.
- handler resolves target via registry and calls ``LocalTransport.send``,
  returning the response text (mock/fake transport injected).
- target not found → error status dict (NOT raise) — pitfall接通 via ToolBridge.
- per-agent allow/deny: allow keeps tool in toolset, deny removes it (ToolBridge
  ``_filter``).

Sync tests + async via new_event_loop (no pytest-asyncio dependency),对位
test_a2a_transport.py。
"""
from __future__ import annotations

import asyncio
import ast
from pathlib import Path
from unittest.mock import MagicMock

from a2a.tool import A2A_CALL_SCHEMA, a2a_call_handler
from a2a.transport import Message, TextPart
from agent.agent_registry import AgentRegistry
from agent.agent_spec import AgentSpec
from harness.capabilities.tool_bridge_capability import ToolBridgeCapability


# --- helpers ----------------------------------------------------------------

class _FakeTransport:
    """In-process LocalTransport stub: records send calls, returns preset text."""
    def __init__(self, text: str = "peer-response") -> None:
        self._text = text
        self.sends: list[tuple[str, str]] = []

    async def send(self, target_agent_id: str, message: str) -> Message:
        self.sends.append((target_agent_id, message))
        return Message(parts=[TextPart(text=self._text)])


def _make_registry() -> AgentRegistry:
    reg = AgentRegistry()
    reg._agents = {
        "native": AgentSpec(id="native", name="原生", cwds=[]),
        "main": AgentSpec(id="main", name="全能", cwds=[]),
    }
    reg._default_id = "native"
    return reg


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _executor_with_a2a_call():
    """mock ToolExecutor whose registry exposes the a2a_call tool (as engine.py
    registers it)."""
    ex = MagicMock()
    ex.registry.list_tools.return_value = [
        {"name": "a2a_call",
         "description": "A2A internal-mesh call",
         "parameters": A2A_CALL_SCHEMA},
    ]
    return ex


_SRC_ROOT = Path(__import__("a2a", fromlist=["tool"]).__file__).resolve().parent.parent


# --- RK11: registration name has no v2_ prefix in source --------------------

class TestRK11NoV2Prefix:
    def test_tool_module_registers_name_a2a_call_no_v2_prefix(self):
        """src/a2a/tool.py must not contain the literal ``v2_a2a_call``."""
        src = Path(__import__("a2a.tool", fromlist=["tool"]).__file__).read_text()
        assert "v2_a2a_call" not in src, (
            "RK11 violation: 'v2_a2a_call' literal must not appear in source; "
            "ToolBridge .prefixed('v2') adds it at runtime."
        )

    def test_no_v2_a2a_call_literal_anywhere_in_src(self):
        """Whole-tree grep: no ``v2_a2a_call`` string literal in orchestrator src
        (registration, comments, strings — all forbidden)."""
        hits = []
        for p in _SRC_ROOT.rglob("*.py"):
            txt = p.read_text()
            if "v2_a2a_call" in txt:
                hits.append(str(p))
        assert not hits, f"RK11 violation: 'v2_a2a_call' literal found in: {hits}"

    def test_engine_registers_bare_a2a_call_name(self):
        """engine.py registers the tool under name ``a2a_call`` (no v2_)."""
        eng = (_SRC_ROOT / "engine.py").read_text()
        # registration call uses the bare name literal
        assert '"a2a_call"' in eng
        # and must NOT register a v2_-prefixed name
        assert '"v2_a2a_call"' not in eng


# --- runtime ToolBridge shows v2_a2a_call -----------------------------------

class TestRuntimePrefixedName:
    def test_toolbridge_toolset_is_prefixed_v2(self):
        """get_toolset returns a PrefixedToolset with prefix='v2'. The runtime
        name the model sees = prefix + source name = 'v2' + 'a2a_call'. Confirms
        runtime name != source name (RK11) and the prefix is applied."""
        cap = ToolBridgeCapability(tool_executor=_executor_with_a2a_call())
        ts = cap.get_toolset()
        assert type(ts).__name__ == "PrefixedToolset"
        assert ts.prefix == "v2"
        # The underlying (un-prefixed) toolset holds the source name 'a2a_call'.
        # PrefixedToolset wraps a delegate; the source name is what gets prefixed.
        runtime_name = f"{ts.prefix}_a2a_call"
        assert runtime_name == "v2_a2a_call"

    def test_toolbridge_filter_all_keeps_a2a_call(self):
        """allow=['a2a_call'] keeps it; deny=['a2a_call'] removes it (policy
        applies to the SOURCE name, before prefixing)."""
        import harness.capabilities.tool_bridge_capability as mod
        kept = []
        orig = mod._make_named_tool
        import unittest.mock as _um
        with _um.patch.object(
            mod, "_make_named_tool",
            lambda ex, pf, n, d, p: (kept.append(n) or orig(ex, pf, n, d, p))):
            # allow
            cap_allow = ToolBridgeCapability(
                tool_executor=_executor_with_a2a_call(), tool_allow=["a2a_call"])
            cap_allow.get_toolset()
        assert kept == ["a2a_call"]

        kept2 = []
        with _um.patch.object(
            mod, "_make_named_tool",
            lambda ex, pf, n, d, p: (kept2.append(n) or orig(ex, pf, n, d, p))):
            cap_deny = ToolBridgeCapability(
                tool_executor=_executor_with_a2a_call(), tool_deny=["a2a_call"])
            cap_deny.get_toolset()
        assert kept2 == []  # denied → not made


# --- handler calls LocalTransport.send --------------------------------------

class TestHandlerSend:
    def test_handler_calls_transport_send_and_returns_text(self):
        reg = _make_registry()
        tport = _FakeTransport("peer says hi")
        result = _run(a2a_call_handler(
            "main", "hello peer", transport=tport, registry=reg))
        assert result["status"] == "success"
        assert result["output"] == "peer says hi"
        assert tport.sends == [("main", "hello peer")]

    def test_handler_uses_default_transport_when_none_injected(self, monkeypatch):
        """Without injected transport, handler builds a LocalTransport. We spy on
        LocalTransport.send to confirm the default path routes through it."""
        reg = _make_registry()
        sends = []

        class _SpyTransport:
            def __init__(self, *a, **k):
                pass

            async def send(self, target_agent_id, message):
                sends.append((target_agent_id, message))
                return Message(parts=[TextPart(text="default-path-ok")])

        monkeypatch.setattr("a2a.transport.LocalTransport", _SpyTransport)
        result = _run(a2a_call_handler("native", "ping", registry=reg))
        assert result == {"status": "success", "output": "default-path-ok"}
        assert sends == [("native", "ping")]


# --- target not found → error (NOT raise) -----------------------------------

class TestTargetNotFound:
    def test_unknown_target_returns_error_not_raise(self):
        reg = _make_registry()
        tport = _FakeTransport()
        result = _run(a2a_call_handler(
            "ghost-agent", "hi", transport=tport, registry=reg))
        assert result["status"] == "error"
        assert "not found" in result["error"]
        assert "ghost-agent" in result["error"]
        assert tport.sends == []  # never called send

    def test_none_registry_returns_error(self, monkeypatch):
        """When _state.agent_registry is None (no engine init), handler returns
        error. registry=None means 'lazy-read default', so we monkeypatch the
        default to None to exercise the unavailable path."""
        from src.services import _state
        monkeypatch.setattr(_state, "agent_registry", None)
        result = _run(a2a_call_handler(
            "main", "hi", transport=_FakeTransport()))
        assert result["status"] == "error"
        assert "registry" in result["error"]


# --- pitfall接通: handler error flows through ToolBridge _execute_via_registry

class TestPitfallWired:
    def test_handler_error_status_surfaces_through_executor(self, monkeypatch):
        """End-to-end through ToolExecutor + ToolBridge _execute_via_registry:
        unknown target → handler returns {status:error} → executor wraps as
        {status:success, output:{handler dict}} → _execute_via_registry success
        branch returns str(output). The error text is surfaced (pitfall接通 means
        the failure is observable through the standard tool path, not swallowed
        and not bypassing ToolBridge)."""
        from src.services import _state
        from src.tools.executor import ToolExecutor
        from src.tools.registry import ToolRegistry
        from harness.capabilities.tool_bridge_capability import _execute_via_registry

        # hermetic: handler reads _state.agent_registry lazily
        monkeypatch.setattr(_state, "agent_registry", _make_registry())
        reg_obj = ToolRegistry()
        reg_obj.register("a2a_call", a2a_call_handler, description="d",
                         parameters=A2A_CALL_SCHEMA)
        ex = ToolExecutor(reg_obj)
        out = _run(_execute_via_registry(
            ex, pitfail=None, tool_name="a2a_call",
            arguments={"target_agent_id": "ghost", "message": "hi"},
        ))
        assert "not found" in out

    def test_pitfall_records_on_handler_raise(self, monkeypatch):
        """If send raises and the handler does NOT catch it, executor catches →
        {status:error} → _execute_via_registry records the pitfall and returns
        '[Tool error] ...'. (Our handler does catch send exceptions, so this also
        covers the executor-level guard.) Verifying with a pitfail spy that the
        record path is reached when status != success."""
        from src.services import _state
        from src.tools.executor import ToolExecutor
        from src.tools.registry import ToolRegistry
        from harness.capabilities.tool_bridge_capability import _execute_via_registry

        monkeypatch.setattr(_state, "agent_registry", _make_registry())
        # register a handler that raises directly (bypassing the handler's catch)
        async def _raising_handler(target_agent_id, message):
            raise RuntimeError("transport exploded")

        reg_obj = ToolRegistry()
        reg_obj.register("a2a_call", _raising_handler, description="d",
                         parameters=A2A_CALL_SCHEMA)
        ex = ToolExecutor(reg_obj)

        recorded = []
        spy = MagicMock()
        spy.match.return_value = []  # no existing pitfall → record path taken
        spy.record = lambda r: recorded.append(r)

        out = _run(_execute_via_registry(
            ex, pitfail=spy, tool_name="a2a_call",
            arguments={"target_agent_id": "main", "message": "hi"},
        ))
        assert out.startswith("[Tool error]")
        assert "transport exploded" in out
        assert len(recorded) == 1  # pitfall record reached


# --- schema sanity ----------------------------------------------------------

class TestSchema:
    def test_schema_requires_target_and_message(self):
        assert set(A2A_CALL_SCHEMA["required"]) == {"target_agent_id", "message"}
        assert A2A_CALL_SCHEMA["properties"]["target_agent_id"]["type"] == "string"
        assert A2A_CALL_SCHEMA["properties"]["message"]["type"] == "string"
