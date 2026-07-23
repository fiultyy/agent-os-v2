"""E2E: A2A internal-mesh consume chain 真跑(GLM) — node E.

把 A/B/C/D 已交付的 consume 链(card→transport→tool→agent_id 追踪)在**进程内**
真跑一次,调真 GLM(智谱 glm-4.7 经 /api/anthropic)。三主断言:

1. **真 GLM 非空响应**(主 gate):``LocalTransport.send("main", ...)`` 返非空文本,
   反映 main 的 SOUL.md/AGENTS.md 身份(总管/委派/助理 语义)。
2. **observe agent_id == "main"**(主 gate,确定性):D 的字段透传到底层——
   拦截 ObserveEmitter.emit 捕获事件,断言 ``agent_id == "main"`` 非 caller native。
   (observe-service 起 HTTP 查询为可选 bonus,作 secondary;主断言不依赖它。)
3. **scope 隔离**(主 gate,确定性):main 的 memory 沉淀落 ``agent_id="main"`` scope,
   不串 native。用 probe MemoryHook 捕获 TURN_END 的 TurnContext.agent_id +
   MemoryItem.agent_id,断言均为 "main"。

确定性优先:assertion 2/3 不依赖 GLM 是否"选择"调工具,只看 ADR-4 scope/agent_id
透传这条 A/B/C/D 已铺好的硬编码链路。assertion 1 依赖真 GLM 在线(已 HTTP 200 验证)。

**绝不 mock model**:不对 ``build_native_agent``/``build_model`` monkeypatch——
这才是 e2e 不是 unit。ObserveEmitter 只包一层 ``emit`` 拦截(不改连接/发语义)。

跑法(必加 LD_PRELOAD sqlite3 fix):
  LD_PRELOAD=/lib/x86_64-linux-gnu/libsqlite3.so.0 \\
    python -m pytest tests/e2e/test_a2a_native_main.py -m e2e -s
"""
from __future__ import annotations

import asyncio
import os
import sys
from typing import Any

import pytest

# sqlite3 fix comes from the e2e conftest (reload of root conftest) + the
# LD_PRELOAD run wrapper. No redundant site-packages hardcoding here.

pytestmark = pytest.mark.e2e


# ── shared helpers ────────────────────────────────────────────────────────

def _run(coro):
    """Run on a fresh loop (no pytest-asyncio dependency, matches unit tests)."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _init_state():
    """Importing src.engine runs the module-level _state bootstrap (lifespan
    analogue): AgentRegistry.load(agents.yaml) → ProfileRegistry.load_all →
    memory_event_bus → tool_registry. Returns src.services._state."""
    # worktree root:AO2_REPO_ROOT 让 agent_registry 在此找 agents.yaml。
    _here = os.path.dirname(os.path.abspath(__file__))
    _repo_root = os.path.abspath(os.path.join(_here, "..", "..", "..", ".."))
    os.environ["AO2_REPO_ROOT"] = _repo_root
    os.environ.setdefault("OBSERVE_URL", "http://127.0.0.1:8002")
    import src.engine  # noqa: F401  (side effect: _state bootstrap)
    from src.services import _state
    return _state


class _ObserveProbe:
    """Wraps ObserveEmitter.emit to capture emitted event dicts (agent_id field).

    ADR-1/D assertion: events the consumed 'main' agent emits carry
    ``agent_id == 'main'`` (target spec id), NOT the caller's. We wrap at the
    emitter boundary — the last hop before observe — so we see exactly what D
    plumbed. No observe-service dependency (deterministic)."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def wrap(self, real_emit):
        async def _wrapped(event: dict[str, Any]) -> None:
            self.events.append(event)
            # best-effort forward (observe may be down; ADR-7 fire-and-forget)
            try:
                await real_emit(event)
            except Exception:
                pass
        return _wrapped


class _MemoryScopeProbe:
    """MemoryHook registered on TURN_END: captures TurnContext.agent_id +
    the working_item / tool_result_item MemoryItem.agent_id. Asserts ADR-4
    scope isolation (main writes scoped to main, never native)."""

    def __init__(self) -> None:
        self.turn_agent_ids: list[str] = []
        self.item_agent_ids: list[str] = []

    async def on_turn_end(self, ctx) -> None:
        self.turn_agent_ids.append(getattr(ctx, "agent_id", None))
        for attr in ("working_item", "tool_result_item"):
            it = getattr(ctx, attr, None)
            if it is not None:
                self.item_agent_ids.append(getattr(it, "agent_id", None))


# ── tests ─────────────────────────────────────────────────────────────────

class TestA2aNativeMainE2E:
    """Real GLM e2e over the A2A consume chain."""

    def test_main_real_glm_response_and_scope_isolation(self):
        """PRIMARY gate, all three assertions.

        send('main') → real build_native_agent → real AnthropicModel → real
        GLM. Then assert (1) non-empty identity-reflecting response,
        (2) observe events carry agent_id='main', (3) memory scoped to 'main'.
        """
        from src.memory.event_bus import EventType
        from src.memory.hooks import HookPriority, MemoryHook

        _state = _init_state()
        assert _state.agent_registry is not None, "AgentRegistry failed to load"
        main_spec = _state.agent_registry.get("main")
        assert main_spec is not None, "main agent missing from registry"

        # wire probes BEFORE the send
        observe_probe = _ObserveProbe()
        memory_probe = _MemoryScopeProbe()

        # make MemoryScopeProbe a real MemoryHook (priority OBSERVER, non-system)
        class _ProbeHook(MemoryHook):
            priority = HookPriority.OBSERVER
            on_turn_end = memory_probe.on_turn_end

        bus = _state.memory_event_bus
        assert bus is not None, "memory_event_bus not initialized"
        bus.register(_ProbeHook(), EventType.TURN_END)

        # wrap ObserveEmitter.emit to capture agent_id (patch at class level,
        # restored after). Real emitter connection proceeds; capture is the goal.
        from src.harness import emit as emit_mod
        _orig_emit = emit_mod.ObserveEmitter.emit

        def _patched_emit(self, event):
            return observe_probe.wrap(_orig_emit.__get__(self, emit_mod.ObserveEmitter))(event)

        emit_mod.ObserveEmitter.emit = _patched_emit  # type: ignore[assignment]

        try:
            from a2a.transport import LocalTransport
            t = LocalTransport()  # reads _state.agent_registry
            msg = _run(t.send(
                "main",
                "用一句话介绍你的角色/职责(你是谁、主要做什么)。",
            ))
        finally:
            emit_mod.ObserveEmitter.emit = _orig_emit  # type: ignore[assignment]

        text = msg.parts[0].text if msg.parts else ""
        text_lower = text.lower() if text else ""

        # ── Assertion 1: real GLM non-empty, reflects main identity ───────
        assert text and text.strip(), f"empty response from main: {text!r}"
        # main 的 AGENTS.md/SOUL.md 身份:总管/委派/助理/管理 subagent。
        # 宽松匹配(中文或语义关键词任一)——GLM 表述自由,断言"反映了身份"而非固定词。
        identity_hits = sum(
            1 for kw in ("总管", "委派", "助理", "管理", "协调", "助手", "全能", "subagent", "代理", "指挥")
            if kw in text or kw in text_lower
        )
        assert identity_hits >= 1, (
            f"main response does not reflect its identity (总管/委派/助理...); "
            f"got: {text[:200]!r}"
        )

        # ── Assertion 2: observe events carry agent_id == 'main' (D plumbing) ─
        assert observe_probe.events, "no observe events captured (D emit not firing)"
        main_events = [e for e in observe_probe.events if e.get("agent_id") == "main"]
        assert main_events, (
            f"no observe events with agent_id='main'; agent_ids seen: "
            f"{sorted({e.get('agent_id') for e in observe_probe.events})}"
        )
        # caller identity must NOT leak (no 'native' as the main-turn agent_id)
        native_leak = [e for e in observe_probe.events if e.get("agent_id") == "native"]
        assert not native_leak, "caller 'native' agent_id leaked into main turn events"

        # ── Assertion 3: scope isolation — memory writes scoped to 'main' ───
        assert memory_probe.turn_agent_ids, (
            "no TURN_END captured (memory writer not scoped to main)"
        )
        assert all(aid == "main" for aid in memory_probe.turn_agent_ids), (
            f"TURN_END agent_id not all 'main': {memory_probe.turn_agent_ids}"
        )
        if memory_probe.item_agent_ids:
            assert all(aid == "main" for aid in memory_probe.item_agent_ids), (
                f"MemoryItem agent_id not all 'main': {memory_probe.item_agent_ids}"
            )

    def test_main_peer_full_capability_stack(self):
        """ADR-4: main runs as PEER with full capability stack (not stripped).
        Verify by intercepting assemble_capabilities — same caps surface as a
        /h session (Observe + MemoryWriter + ToolBridge + Guardrail + ...).
        Deterministic (no GLM): the consume path must request the full stack."""
        _state = _init_state()
        captured: dict[str, Any] = {}

        from src.harness import routes as routes_mod
        _orig = routes_mod.assemble_capabilities

        def _spy(spec, **kw):
            caps, model_name = _orig(spec, **kw)
            captured["cap_ids"] = [getattr(c, "id", repr(c)) for c in caps]
            captured["agent_id_for_scope"] = kw.get("agent_id_for_scope")
            return caps, model_name

        routes_mod.assemble_capabilities = _spy  # type: ignore[assignment]
        try:
            from a2a.transport import LocalTransport
            from src.harness import emit as emit_mod

            class _NoNet:
                async def connect(self): pass
                async def close(self): pass
                async def emit(self, event): pass

            emit_mod.ObserveEmitter = lambda *a, **k: _NoNet()  # type: ignore
            t = LocalTransport()
            _run(t.send("main", "x"))
        finally:
            routes_mod.assemble_capabilities = _orig  # type: ignore[assignment]

        cap_ids = captured.get("cap_ids", [])
        assert "main" == captured.get("agent_id_for_scope"), "scope not main"
        # peer parity: the same core caps a /h session gets.
        for required in ("observe", "memory_writer", "tool_bridge", "guardrail"):
            assert required in cap_ids, (
                f"main consumed agent missing peer cap '{required}'; has {cap_ids}"
            )
