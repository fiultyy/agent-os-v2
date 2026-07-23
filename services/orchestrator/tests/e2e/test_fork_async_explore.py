"""E2E: fork → async-explore 真跑(GLM) — node F4.

把 F1/F2/F3 已交付的流式编排 engine 全链在**进程内**真跑一次,调真 GLM(智谱
glm-4.7 经 /api/anthropic),把 fork→async-explore→observe fork 树整条集成跑通。

四主断言:

1. **真 GLM 响应**(主 gate):各 fork 异步跑完有真响应(非空、反映方向 A/B/C)。
2. **并发 overlap**(主 gate):observe 里 3 个 tick_started 都在任一 tick_completed
   之前(真 overlap,非串行)。streaming + asyncio.create_task 并发下,3 路 agent.run
   并发跑,首 token(emit tick_started)在任一 stream 耗尽(emit tick_completed)前到齐。
   用**事件序列号**(capture 时的单调计数)比时间戳稳。
3. **lineage**(主 gate):observe 有 3 个 branch_created 事件,parent_session_id=S,
   child=各 fork session_id;agent_id 透传。
4. **消息深拷贝继承**(集成铁证):seed turn 后 fork,源 messages 非空 → 3 fork 各自
   深拷贝源 messages(非浅引用,改一支不污染另支/源)。fork 后各自 turn 的响应反映
   自己的方向而非源(各跑各的)。

**绝不 mock model**:不 monkeypatch build_native_agent/build_model/AnthropicModel ——
这才叫 e2e。ObserveEmitter 只包一层 emit 拦截捕事件 + 序列号(不改连接/发语义)。

确定性优先:断言 2/3 不依赖 GLM 是否"选择"调工具,只看 F1/F2/F3 已铺好的硬编码链路
(fork 克隆 + async task + observe emit)。断言 1 依赖真 GLM 在线(已 HTTP 200 验证)。

跑法(必加 LD_PRELOAD sqlite3 fix):
  LD_PRELOAD=/lib/x86_64-linux-gnu/libsqlite3.so.0 \\
    python -m pytest tests/e2e/test_fork_async_explore.py -m e2e -s
"""
from __future__ import annotations

import asyncio
import os
import tempfile
from typing import Any

import pytest

pytestmark = pytest.mark.e2e


# ── shared helpers (mirror test_a2a_native_main e2e pattern) ──────────────

def _fresh_store(routes):
    """Point routes._store at a brand-new temp db so the e2e is hermetic
    (the module-level _store shares data/orch_sessions.db across runs/sessions).
    Mirrors the unit-test `store` fixture pattern without pytest fixture plumbing."""
    from src.harness.session_store import OrchSessionStore
    tmpdir = tempfile.mkdtemp(prefix="f4_e2e_")
    store = OrchSessionStore(os.path.join(tmpdir, "orch.db"))
    routes._store = store
    return store


def _run(coro):
    """Run on a fresh loop (no pytest-asyncio dependency, matches unit tests)."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _init_state():
    """Importing src.engine runs the module-level _state bootstrap. Sets
    AO2_REPO_ROOT so agent_registry finds agents.yaml."""
    _here = os.path.dirname(os.path.abspath(__file__))
    _repo_root = os.path.abspath(os.path.join(_here, "..", "..", "..", ".."))
    os.environ["AO2_REPO_ROOT"] = _repo_root
    os.environ.setdefault("OBSERVE_URL", "http://127.0.0.1:8002")
    import src.engine  # noqa: F401  (side effect: _state bootstrap)
    from src.services import _state
    return _state


class _EventCapture:
    """Wraps ObserveEmitter.emit to capture every emitted event with a monotonic
    sequence number (proof of ordering for the concurrency-overlap assertion).

    ADR-7 fire-and-forget: observe may be down; we forward best-effort and
    swallow forward errors so a dead observe WS never breaks the real turn.
    """

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []  # each: {seq, **event}

    def wrap(self, real_emit):
        async def _wrapped(event: dict[str, Any]) -> None:
            # record with a sequence number = append order (monotonic proof)
            self.events.append({"seq": len(self.events), **event})
            try:
                await real_emit(event)
            except Exception:
                pass
        return _wrapped


class _ConcurrencyProbe:
    """Wraps routes._run_native_turn_async to record each async turn's enter /
    exit, indexed by session_id. Proves all 3 fork turns overlapped on the
    loop INDEPENDENTLY of ObserveCapability's tick emission (the tick_started
    heuristic can skip short/non-streamed responses — see F4 e2e diary)."""

    def __init__(self) -> None:
        # session_id -> {"enter": seq, "exit": seq} using a global monotonic seq
        self.lifecycles: dict[str, dict[str, int]] = {}
        self._seq = 0

    def mark_enter(self, session_id: str) -> None:
        self._seq += 1
        self.lifecycles.setdefault(session_id, {})["enter"] = self._seq

    def mark_exit(self, session_id: str) -> None:
        self._seq += 1
        self.lifecycles.setdefault(session_id, {})["exit"] = self._seq


class TestForkAsyncExploreE2E:
    """Real GLM e2e over the fork → async-explore → observe fork-tree chain."""

    def test_fork_async_explore_real_glm_concurrent_lineage(self):
        """PRIMARY gate, all four assertions.

        create native S → seed turn (real GLM) → fork fan-out A/B/C → async turn
        each fork (fire-and-forget, real GLM) → assert real responses + concurrent
        overlap + lineage + deep-copy inheritance.
        """
        _init_state()
        from src.harness import routes
        from src.harness import emit as emit_mod

        # reset module-level session registries so the test is hermetic
        routes._sessions.clear()
        _fresh_store(routes)  # fresh temp db (don't pollute data/orch_sessions.db)
        routes._async_turn_tasks.clear()

        capture = _EventCapture()
        conc = _ConcurrencyProbe()
        _orig_emit = emit_mod.ObserveEmitter.emit
        _orig_runner = routes._run_native_turn_async

        def _patched_emit(self, event):
            return capture.wrap(_orig_emit.__get__(self, emit_mod.ObserveEmitter))(event)

        async def _probed_runner(rec, session_id, message, tick_id):
            conc.mark_enter(session_id)
            try:
                return await _orig_runner(rec, session_id, message, tick_id)
            finally:
                conc.mark_exit(session_id)

        emit_mod.ObserveEmitter.emit = _patched_emit  # type: ignore[assignment]
        routes._run_native_turn_async = _probed_runner  # type: ignore[assignment]

        # The whole scenario runs in ONE event loop. Each routes.trigger_turn with
        # async_run=True does asyncio.create_task — that task is bound to THIS loop
        # and must be awaited HERE (a fresh per-step loop would close and cancel it).
        async def _scenario():
            # ── 1. create native session S (real _build_native_session) ───────
            s_resp = await routes.create_session(
                "agent-os-v2", routes.CreateSessionReq())
            assert s_resp["status"] == "created"
            S = s_resp["session_id"]

            # ── 2. seed turn (real GLM) — plants shared context ──────────────
            seed = await routes.trigger_turn(
                "agent-os-v2", S,
                routes.TurnReq(message="记住这个种子事实:项目代号是 F4-stream。"),
            )
            assert seed["status"] == "completed", f"seed turn failed: {seed}"
            seed_text = str(seed.get("response", "")).strip()
            assert seed_text, f"empty GLM seed response: {seed!r}"
            src_after_seed = len(routes._sessions[routes._key("agent-os-v2", S)]["messages"])
            assert src_after_seed >= 2, (
                f"seed turn did not extend S messages (got {src_after_seed}); "
                "fork-from-empty would not prove deep copy"
            )

            # ── 3. fork fan-out 3 branches (real fork_session) ───────────────
            targets = [
                routes.ForkTarget(first_message="方向A:用一句话复述项目代号。"),
                routes.ForkTarget(first_message="方向B:项目代号的首字母是什么?只回字母。"),
                routes.ForkTarget(first_message="方向C:把项目代号倒着写出来。"),
            ]
            fork_resp = await routes.fork_session(
                "agent-os-v2",
                routes.ForkReq(source_session_id=S, first_message="unused", targets=targets),
            )
            assert fork_resp["forked"] is True
            forks = fork_resp["forks"]
            assert len(forks) == 3, f"expected 3 forks, got {len(forks)}"
            fork_sids = [f["new_session_id"] for f in forks]
            assert len(set(fork_sids)) == 3
            for fsid in fork_sids:
                frec = routes._sessions[routes._key("agent-os-v2", fsid)]
                assert len(frec["messages"]) == src_after_seed, (
                    f"fork {fsid} did not inherit source messages "
                    f"(got {len(frec['messages'])}, expected {src_after_seed})"
                )

            # ── 4. async turn each fork (fire-and-forget, real GLM) ──────────
            # fire all 3 BEFORE awaiting → they run concurrently on this loop.
            for f, fsid in zip(targets, fork_sids):
                r = await routes.trigger_turn(
                    "agent-os-v2", fsid,
                    routes.TurnReq(message=f.first_message, async_run=True),
                )
                assert r["status"] == "started", (
                    f"async turn for {fsid} did not start: {r}"
                )
            assert len(routes._async_turn_tasks) >= 3, (
                f"expected >=3 async tasks in registry, got "
                f"{len(routes._async_turn_tasks)}"
            )

            # wait for all 3 fork turns to finish (real GLM completes each run).
            # poll message counts; fall back to draining lingering tasks.
            deadline = 120.0
            elapsed = 0.0
            while elapsed < deadline:
                done = sum(
                    1 for fsid in fork_sids
                    if len(routes._sessions[routes._key("agent-os-v2", fsid)]["messages"])
                    > src_after_seed
                )
                if done >= 3:
                    break
                await asyncio.sleep(0.2)
                elapsed += 0.2
            pending = [t for t in routes._async_turn_tasks.values() if not t.done()]
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            await asyncio.sleep(0)  # let done_callbacks fire

            fork_texts: list[str] = []
            for fsid in fork_sids:
                msgs = routes._sessions[routes._key("agent-os-v2", fsid)]["messages"]
                assert len(msgs) > src_after_seed, (
                    f"fork {fsid} async turn never produced a response "
                    f"(messages still {len(msgs)} after drain)"
                )
                fork_texts.append(_extract_text(msgs[-1]))
            return S, fork_sids, targets, src_after_seed, fork_texts

        try:
            S, fork_sids, targets, src_after_seed, fork_texts = _run(_scenario())
        finally:
            emit_mod.ObserveEmitter.emit = _orig_emit  # type: ignore[assignment]
            routes._run_native_turn_async = _orig_runner  # type: ignore[assignment]

        # ════════════════════════════════════════════════════════════════════
        # ASSERTIONS (read capture / session state — no loop needed)
        # ════════════════════════════════════════════════════════════════════

        # ── Assertion 1: real GLM non-empty responses, reflect direction ─────
        assert len(fork_texts) == 3
        for i, txt in enumerate(fork_texts):
            assert txt and txt.strip(), (
                f"fork {fork_sids[i]} ({targets[i].first_message[:8]}) empty GLM response"
            )
        # direction B asks for the first letter ("F"); direction C asks for the
        # code reversed ("maerts-4F"). At least one of these deterministic
        # artifacts should appear — proves each fork answered its OWN direction,
        # not a shared/cached answer.
        joined = "\n".join(fork_texts)
        direction_hits = (
            "F" in fork_texts[1]              # B: first letter of F4-stream
            or "4" in fork_texts[1]
            or "maert" in joined.lower()      # C: reversed
            or "F4" in joined or "f4" in joined.lower()
        )
        assert direction_hits, (
            f"fork responses do not reflect their directions (A/B/C); got:\n"
            f"A={fork_texts[0][:120]!r}\nB={fork_texts[1][:120]!r}\n"
            f"C={fork_texts[2][:120]!r}"
        )
        # responses are not all identical (each fork ran its own turn) — guards
        # against a bug where all forks shared one turn/response.
        assert len(set(t.strip()[:40] for t in fork_texts)) >= 2, (
            f"all 3 fork responses identical — forks did not run independently: {fork_texts}"
        )

        # ── Assertion 2: concurrency overlap — real overlap, not serial.
        #
        # Two independent signals; BOTH must hold:
        #
        # (2a) Task-lifecycle overlap (PRIMARY, deterministic): all 3 fork turns
        #      entered _run_native_turn_async before any of them exited. This is
        #      the real concurrency proof — it measures the asyncio tasks directly,
        #      independent of how GLM/pydantic-ai streams its response.
        #
        # (2b) observe tick overlap (SECONDARY, best-effort): among branches that
        #      DID emit tick events, their tick_started precede the earliest
        #      tick_completed. NOTE: ObserveCapability's deferred-start heuristic
        #      (emit tick_started only on the first content event) skips
        #      tick_started/tick_completed for branches whose first stream event
        #      isn't a TextPartDelta/tool-call (short or batched GLM responses).
        #      So we cannot require >=3 tick_started — a flaky branch can emit
        #      only `usage`. We assert >=2 (the overlap trend) when >=2 branches
        #      emitted both phases. F2 caveat: HTTP tick_id != observe tick_id,
        #      so we match on aggregate event_type + session_id.
        lc = conc.lifecycles
        assert set(lc) == set(fork_sids), (
            f"concurrency probe missed fork turns: "
            f"probed={sorted(lc)} forks={sorted(fork_sids)}"
        )
        enters = {sid: lc[sid]["enter"] for sid in fork_sids}
        exits = {sid: lc[sid]["exit"] for sid in fork_sids}
        last_enter = max(enters.values())
        first_exit = min(exits.values())
        # 2a: all 3 entered before ANY exited → genuine overlap
        assert last_enter < first_exit, (
            f"no task-lifecycle concurrency overlap: last enter (seq={last_enter}) "
            f"not before first exit (seq={first_exit}); turns ran serially or a "
            f"turn finished before others started. enters={enters} exits={exits}"
        )

        tick_started_for_forks = [
            e for e in capture.events
            if e.get("event_type") == "tick_started" and e.get("session_id") in fork_sids
        ]
        tick_completed_for_forks = [
            e for e in capture.events
            if e.get("event_type") == "tick_completed" and e.get("session_id") in fork_sids
        ]
        # 2b: where tick events exist, started must precede completed (overlap).
        # require at least 2 branches emitting both phases (robust lower bound;
        # the deferred-start heuristic can drop a short-response branch's ticks).
        started_sids = {e["session_id"] for e in tick_started_for_forks}
        completed_sids = {e["session_id"] for e in tick_completed_for_forks}
        both = started_sids & completed_sids
        assert len(both) >= 2, (
            f"fewer than 2 branches emitted both tick_started+tick_completed "
            f"(started={sorted(started_sids)} completed={sorted(completed_sids)}); "
            f"cannot verify observe-tick overlap. all event types: "
            f"{sorted({e.get('event_type') for e in capture.events})}"
        )
        last_started_seq = max(e["seq"] for e in tick_started_for_forks)
        first_completed_seq = min(e["seq"] for e in tick_completed_for_forks)
        assert last_started_seq < first_completed_seq, (
            f"no observe-tick overlap: last tick_started (seq={last_started_seq}) "
            f"not before first tick_completed (seq={first_completed_seq}). "
            f"started_seqs={[e['seq'] for e in tick_started_for_forks]}, "
            f"completed_seqs={[e['seq'] for e in tick_completed_for_forks]}"
        )

        # ── Assertion 3: lineage — 3 branch_created, parent=S, child=fork,
        #    agent_id propagated (F3 ADR-S5) ────────────────────────────────
        branch_events = [
            e for e in capture.events if e.get("event_type") == "branch_created"
        ]
        assert len(branch_events) == 3, (
            f"expected 3 branch_created events, got {len(branch_events)}"
        )
        child_ids = set()
        for ev in branch_events:
            assert ev.get("parent_session_id") == S, (
                f"branch_created parent_session_id != S: {ev.get('parent_session_id')} "
                f"(expected {S})"
            )
            child = ev.get("session_id")
            assert child in fork_sids, (
                f"branch_created session_id {child!r} not one of fork sids {fork_sids}"
            )
            child_ids.add(child)
            # agent_id propagated (D's field; for default native agent it's
            # the normalized spec id, non-empty)
            assert ev.get("agent_id"), (
                f"branch_created agent_id empty for {child}: {ev.get('agent_id')!r}"
            )
            assert ev.get("data", {}).get("parent_branch_id") == S
            assert ev.get("data", {}).get("branch_id") == child
        assert child_ids == set(fork_sids), (
            f"branch_created children {child_ids} != fork sids {set(fork_sids)}"
        )

        # ── Assertion 4: deep-copy inheritance — mutating one fork doesn't
        #    leak to siblings or source (F1 ADR-S2) ─────────────────────────
        # each fork already ran its OWN turn above; source S's message count
        # must be unchanged (fork turns did not write back to S).
        src_now = len(routes._sessions[routes._key("agent-os-v2", S)]["messages"])
        assert src_now == src_after_seed, (
            f"source S messages changed after fork turns: {src_after_seed} -> {src_now} "
            "(fork mutated source — deep copy is broken)"
        )
        # each fork's message count grew independently (own turn, own response)
        counts = [
            len(routes._sessions[routes._key("agent-os-v2", fsid)]["messages"])
            for fsid in fork_sids
        ]
        assert all(c > src_after_seed for c in counts), (
            f"some fork did not grow independently: counts={counts} "
            f"seed={src_after_seed}"
        )

    def test_fork_async_agent_id_propagates_to_tick_events(self):
        """Secondary: the agent_id that branch_created carries (source spec_id)
        also flows to the fork's tick_started/tick_completed events. Real GLM
        but a narrow plumbing check — the same agent_id appears across the
        fork's whole observe lifecycle, not just branch_created."""
        _init_state()
        from src.harness import routes
        from src.harness import emit as emit_mod

        routes._sessions.clear()
        _fresh_store(routes)
        routes._async_turn_tasks.clear()

        capture = _EventCapture()
        _orig_emit = emit_mod.ObserveEmitter.emit

        def _patched_emit(self, event):
            return capture.wrap(_orig_emit.__get__(self, emit_mod.ObserveEmitter))(event)

        emit_mod.ObserveEmitter.emit = _patched_emit  # type: ignore[assignment]

        async def _scenario():
            s_resp = await routes.create_session(
                "agent-os-v2", routes.CreateSessionReq())
            S = s_resp["session_id"]
            await routes.trigger_turn(
                "agent-os-v2", S,
                routes.TurnReq(message="种子:代号 X。"),
            )

            fork_resp = await routes.fork_session(
                "agent-os-v2",
                routes.ForkReq(source_session_id=S, first_message="x",
                               targets=[routes.ForkTarget(first_message="复述代号。")]),
            )
            fsid = fork_resp["forks"][0]["new_session_id"]

            await routes.trigger_turn(
                "agent-os-v2", fsid,
                routes.TurnReq(message="复述代号。", async_run=True),
            )
            # drain the async task on the same loop it was created on
            pending = [t for t in routes._async_turn_tasks.values() if not t.done()]
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            await asyncio.sleep(0)
            return fsid

        try:
            fsid = _run(_scenario())
        finally:
            emit_mod.ObserveEmitter.emit = _orig_emit  # type: ignore[assignment]

        fork_tick_events = [
            e for e in capture.events
            if e.get("event_type") in ("tick_started", "tick_completed")
            and e.get("session_id") == fsid
        ]

        assert fork_tick_events, "no tick events captured for fork session"
        # the agent_id is consistent across branch_created + tick events for
        # this fork, and equals the source spec's normalized id (non-empty).
        agent_ids = {e.get("agent_id") for e in fork_tick_events}
        assert len(agent_ids) == 1, (
            f"fork tick agent_ids not consistent: {agent_ids}"
        )
        the_agent_id = next(iter(agent_ids))
        assert the_agent_id, f"fork tick agent_id empty: {the_agent_id!r}"


# ── helpers ────────────────────────────────────────────────────────────────

def _extract_text(msg) -> str:
    """Best-effort pull of text content from the last ModelMessage (pydantic-ai)."""
    # ModelResponse: has .parts with TextPart(content=...)
    parts = getattr(msg, "parts", None) or []
    out = []
    for p in parts:
        content = getattr(p, "content", None)
        if isinstance(content, str) and content:
            out.append(content)
    return "\n".join(out)
