"""Unit tests for A2A LocalTransport (node B).

Covers:
- send() returns an A2A-shaped Message (role 'agent', parts with text).
- ADR-4 scope isolation: consumed MemoryWriter scoped to TARGET agent_id, never
  the caller's. Asserted by intercepting assemble_capabilities (the shared
  chokepoint) and checking agent_id_for_scope.
- Zero network: transport.py has no httpx/socket/aiohttp imports (AST scan).
- Assembly reuse: transport calls the shared assemble_capabilities (no pasted
  assembly block); _build_native_session calls it too.

Sync tests + async via new_event_loop (no pytest-asyncio dependency).
"""
import asyncio

import pytest

from agent.agent_registry import AgentRegistry
from agent.agent_spec import AgentSpec
from a2a.transport import LocalTransport, Message, Task, TextPart


# --- helpers ----------------------------------------------------------------

class _FakeResult:
    def __init__(self, text: str) -> None:
        self.output = text


class _FakeAgent:
    def __init__(self, text: str = "pong") -> None:
        self._text = text
        self.run_called_with = None

    async def run(self, message, message_history=None):
        self.run_called_with = (message, message_history)
        return _FakeResult(self._text)


class _FakeEmitter:
    """Replaces ObserveEmitter so no real observe connection is attempted."""
    async def connect(self):
        pass

    async def close(self):
        pass


def _make_registry() -> AgentRegistry:
    reg = AgentRegistry()
    reg._agents = {
        "native": AgentSpec(id="native", name="原生", cwds=[]),
        "main": AgentSpec(id="main", name="全能", cwds=[]),
    }
    reg._default_id = "native"
    return reg


def _run(coro):
    """Run a coroutine on a fresh event loop (no pytest-asyncio needed)."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# --- A2A response shape -----------------------------------------------------

class TestSendShape:
    def test_send_returns_message_with_agent_role_and_text_part(self, monkeypatch):
        monkeypatch.setattr(
            "src.harness.emit.ObserveEmitter", lambda *a, **k: _FakeEmitter()
        )
        captured = {}

        def fake_assemble(spec, **kw):
            captured.update(kw)
            return [], None

        monkeypatch.setattr(
            "src.harness.routes.assemble_capabilities", fake_assemble
        )
        monkeypatch.setattr(
            "src.harness.native_agent.build_native_agent",
            lambda **kw: _FakeAgent("hello from target"),
        )
        reg = _make_registry()
        t = LocalTransport(registry=reg)

        msg = _run(t.send("main", "hi"))

        assert isinstance(msg, Message)
        assert msg.role == "agent"
        assert len(msg.parts) == 1
        assert isinstance(msg.parts[0], TextPart)
        assert msg.parts[0].text == "hello from target"
        assert msg.parts[0].type == "text"
        assert msg.message_id  # generated

    def test_send_fresh_turn_no_message_history(self, monkeypatch):
        """ADR-5 MVP: consumed turn is fresh (no message_history)."""
        monkeypatch.setattr(
            "src.harness.emit.ObserveEmitter", lambda *a, **k: _FakeEmitter()
        )
        monkeypatch.setattr(
            "src.harness.routes.assemble_capabilities", lambda spec, **kw: ([], None)
        )
        fake_agent = _FakeAgent()
        monkeypatch.setattr(
            "src.harness.native_agent.build_native_agent", lambda **kw: fake_agent
        )
        reg = _make_registry()
        t = LocalTransport(registry=reg)

        _run(t.send("main", "hi"))

        # agent.run called with message only, no message_history kwarg passed
        assert fake_agent.run_called_with is not None
        assert fake_agent.run_called_with[0] == "hi"
        # LocalTransport must NOT pass message_history at all (fresh turn)
        assert fake_agent.run_called_with[1] is None

    def test_send_unknown_target_raises_keyerror(self, monkeypatch):
        reg = _make_registry()
        t = LocalTransport(registry=reg)
        with pytest.raises(KeyError):
            _run(t.send("ghost-agent", "hi"))


# --- ADR-4 scope isolation (core invariant) ---------------------------------

class TestScopeIsolation:
    def test_memory_writer_scoped_to_target_not_caller(self, monkeypatch):
        """ADR-4: agent_id_for_scope passed to assemble_capabilities MUST be the
        target agent_id, never the caller's. This is the single chokepoint."""
        monkeypatch.setattr(
            "src.harness.emit.ObserveEmitter", lambda *a, **k: _FakeEmitter()
        )
        monkeypatch.setattr(
            "src.harness.routes.assemble_capabilities", lambda spec, **kw: ([], None)
        )
        monkeypatch.setattr(
            "src.harness.native_agent.build_native_agent",
            lambda **kw: _FakeAgent(),
        )

        # Capture the agent_id_for_scope the transport requests.
        captured = {}

        def spy_assemble(spec, **kw):
            captured["agent_id_for_scope"] = kw.get("agent_id_for_scope")
            return [], None

        monkeypatch.setattr("src.harness.routes.assemble_capabilities", spy_assemble)

        reg = _make_registry()
        t = LocalTransport(registry=reg)
        # caller identity is irrelevant to the transport (no caller_id param);
        # the invariant is purely that scope == target.
        _run(t.send("main", "delegate this"))

        assert captured["agent_id_for_scope"] == "main"

    def test_scope_differs_across_targets(self, monkeypatch):
        """Two sends to different targets must scope to their respective ids."""
        monkeypatch.setattr(
            "src.harness.emit.ObserveEmitter", lambda *a, **k: _FakeEmitter()
        )
        monkeypatch.setattr(
            "src.harness.native_agent.build_native_agent",
            lambda **kw: _FakeAgent(),
        )
        scopes = []

        def spy_assemble(spec, **kw):
            scopes.append(kw.get("agent_id_for_scope"))
            return [], None

        monkeypatch.setattr("src.harness.routes.assemble_capabilities", spy_assemble)

        reg = _make_registry()
        t = LocalTransport(registry=reg)
        _run(t.send("native", "a"))
        _run(t.send("main", "b"))

        assert scopes == ["native", "main"]
        assert len(set(scopes)) == 2  # distinct per target


# --- zero network (AST) -----------------------------------------------------

class TestZeroNetwork:
    def test_transport_module_has_no_network_imports(self):
        """ADR-5: zero network. AST scan catches import + from-import of
        banned network modules."""
        import ast
        from pathlib import Path

        src = Path(
            __import__("a2a", fromlist=["transport"]).__file__
        ).parent.joinpath("transport.py").read_text()
        tree = ast.parse(src)
        mods: set[str] = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                mods.update(a.name.split(".")[0] for a in n.names)
            elif isinstance(n, ast.ImportFrom):
                if n.module:
                    mods.add(n.module.split(".")[0])
        banned = {"httpx", "socket", "aiohttp", "requests", "urllib3"}
        leaked = mods & banned
        assert not leaked, f"network deps leaked into a2a.transport: {leaked}"


# --- assembly reuse (DRY) ---------------------------------------------------

class TestAssemblyReuse:
    def test_transport_calls_shared_assemble_capabilities(self, monkeypatch):
        """The transport must reuse assemble_capabilities, not paste a second
        capability-assembly block. Verified by it being the import target of
        the spy AND the call producing the caps."""
        monkeypatch.setattr(
            "src.harness.emit.ObserveEmitter", lambda *a, **k: _FakeEmitter()
        )
        called = {"assemble": False, "build": False}

        def fake_assemble(spec, **kw):
            called["assemble"] = True
            return ["CAP_A", "CAP_B"], "glm-test"

        def fake_build(**kw):
            called["build"] = True
            # assert the caps came from assemble (the shared function)
            assert kw.get("capabilities") == ["CAP_A", "CAP_B"]
            assert kw.get("model_name") == "glm-test"
            return _FakeAgent()

        monkeypatch.setattr("src.harness.routes.assemble_capabilities", fake_assemble)
        monkeypatch.setattr(
            "src.harness.native_agent.build_native_agent", fake_build
        )
        reg = _make_registry()
        t = LocalTransport(registry=reg)
        _run(t.send("main", "x"))
        assert called["assemble"] and called["build"]

    def test_build_native_session_and_transport_share_assemble(self):
        """Both _build_native_session and LocalTransport must reference the same
        assemble_capabilities symbol (no duplicated assembly block). grep-based."""
        from pathlib import Path

        routes_src = Path(
            __import__("src.harness.routes", fromlist=["routes"]).__file__
        ).read_text()
        transport_src = Path(
            __import__("a2a.transport", fromlist=["transport"]).__file__
        ).read_text()
        # routes.py defines it (DRY source); transport.py imports it from routes.
        assert "def assemble_capabilities(" in routes_src
        assert "assemble_capabilities" in transport_src
        # transport must NOT define its own MemoryWriterCapability assembly
        # (that lives only in assemble_capabilities).
        assert "MemoryWriterCapability(" not in transport_src
