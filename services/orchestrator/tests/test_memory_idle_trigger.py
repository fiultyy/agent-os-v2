"""Tests for memory-maintenance idle-trigger strategy (tasks A/B/C/D).

Covers the four implementation pieces:

A. origin semantic fix — ``store_memory`` forwards the ``origin`` query param
   to ``MemoryService.store`` (default ``foreground`` for backward compat,
   ``agent`` marks the row ``origin=AGENT`` so it becomes digestible).

B. MemoryDBWatcher tiered idle-trigger:
   - extract fires only when write-idle ≥ idle_threshold AND the per-agent
     extract cadence elapsed AND pending-extract (origin=AGENT, un-extracted)
     > 0;
   - a fresh write during a poll window DEFERS the LLM tiers (debounce);
   - the extract cadence (extract_interval) rate-limits re-extraction;
   - each extracted item is marked ``metadata.extracted=True`` (dedupe).

C. observability — ``maintenance_snapshot`` reports the clocks + pending count.

D. P0 red-line — FOREGROUND memories are NEVER extracted, even after a long
   idle window (only origin=AGENT is digestible).

The watcher's idle detection reads ``MAX(updated_at)`` as the write proxy and
compares ISO timestamps; tests manipulate ``last_write_ts`` / clock fields
directly (and the store's ``touch()``) rather than sleeping, for speed and
determinism.
"""

import os
import sys
from datetime import datetime, timezone, timedelta
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from src.api.models import StoreMemoryRequest
from src.api.routes import memory as memory_route
from src.services import _state
from src.memory.db_watcher import MemoryDBWatcher, _parse_iso, _now_ts
from src.memory.types import (
    MemoryItem, MemoryFilter, MemoryType, MemoryScope, MemoryOrigin,
)
from src.memory.store import InMemoryStore
from src.memory.service import MemoryService


# ── helpers / fakes ────────────────────────────────────────────────


def _svc(store=None) -> MemoryService:
    return MemoryService(store=store or InMemoryStore())


def _iso(seconds_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)).isoformat()


class _FakeIngestor:
    """Records every ingest() call and returns a triggered result.

    Tracks received origins so a test can assert FOREGROUND was never passed
    to extraction (P0).
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def ingest(self, *, memory_id, content, agent_id, session_id, origin, timeout=None):
        self.calls.append({
            "memory_id": memory_id, "content": content, "agent_id": agent_id,
            "session_id": session_id, "origin": origin, "timeout": timeout,
        })
        # Simulate the real P0 guard: FOREGROUND is skipped (defence in depth).
        ov = origin.value if hasattr(origin, "value") else str(origin)
        if ov == MemoryOrigin.FOREGROUND.value:
            return _Result(skipped=True)
        return _Result(triggered=True)


class _Result:
    def __init__(self, *, triggered=False, skipped=False, error=""):
        self.triggered = triggered
        self.skipped = skipped
        self.error = error
        self.entities_added = 1 if triggered else 0
        self.relations_added = 0
        self.importance = 0.5
        self.identity_category = "KNOWLEDGE" if triggered else "NONE"
        self.degraded = False


def _make_watcher(svc: MemoryService, **kw) -> MemoryDBWatcher:
    """Build a watcher with small idle thresholds for fast tests."""
    w = MemoryDBWatcher(svc, poll_interval=kw.get("poll_interval", 1.0))
    w.idle_threshold = kw.get("idle_threshold", 60.0)
    w.extract_interval = kw.get("extract_interval", 300.0)
    w.consolidate_threshold = kw.get("consolidate_threshold", 300.0)
    return w


async def _store_agent_mem(svc, content="agent work", agent_id="a1", mid=None) -> str:
    ref = await svc.store(
        content=content, agent_id=agent_id, origin=MemoryOrigin.AGENT,
    )
    if mid:
        # allow tests to pin an id if needed
        item = await svc.get(ref.id)
        item.id = mid
    return ref.id


async def _store_fg_mem(svc, content="user note", agent_id="a1") -> str:
    ref = await svc.store(
        content=content, agent_id=agent_id, origin=MemoryOrigin.FOREGROUND,
    )
    return ref.id


# ── A. store_memory origin passthrough ─────────────────────────────


class TestStoreMemoryOriginPassthrough:
    @pytest.mark.asyncio
    async def test_default_origin_is_foreground(self, monkeypatch):
        """POST /memories with no origin param → FOREGROUND (backward compat)."""
        svc = _svc()
        monkeypatch.setattr(_state, "memory_service", svc)
        req = StoreMemoryRequest(content="hello", agent_id="a1")
        resp = await memory_route.store_memory(req)
        item = await svc.get(resp["id"])
        assert item.origin == MemoryOrigin.FOREGROUND

    @pytest.mark.asyncio
    async def test_explicit_agent_origin(self, monkeypatch):
        """POST /memories?origin=agent → origin=AGENT (digestible)."""
        svc = _svc()
        monkeypatch.setattr(_state, "memory_service", svc)
        req = StoreMemoryRequest(content="agent dialogue", agent_id="a1")
        resp = await memory_route.store_memory(req, origin="agent")
        item = await svc.get(resp["id"])
        assert item.origin == MemoryOrigin.AGENT

    @pytest.mark.asyncio
    async def test_explicit_foreground_origin(self, monkeypatch):
        svc = _svc()
        monkeypatch.setattr(_state, "memory_service", svc)
        req = StoreMemoryRequest(content="user msg", agent_id="a1")
        resp = await memory_route.store_memory(req, origin="foreground")
        item = await svc.get(resp["id"])
        assert item.origin == MemoryOrigin.FOREGROUND

    @pytest.mark.asyncio
    async def test_old_call_signature_unchanged(self, monkeypatch):
        """A caller that passes only the request body behaves as before
        (no origin kwarg) — the route has a default param."""
        svc = _svc()
        monkeypatch.setattr(_state, "memory_service", svc)
        req = StoreMemoryRequest(content="legacy", agent_id="a1")
        # call WITHOUT the origin kwarg (legacy caller)
        resp = await memory_route.store_memory(req)
        assert resp["id"]
        assert (await svc.get(resp["id"])).origin == MemoryOrigin.FOREGROUND


# ── B. MemoryDBWatcher tiered idle-trigger ─────────────────────────


class TestIdleTriggerExtract:
    @pytest.mark.asyncio
    async def test_extract_fires_when_idle_and_pending(self, monkeypatch):
        """Idle ≥ idle_threshold + cadence elapsed + pending AGENT backlog
        → extract fires, items marked extracted=True."""
        svc = _svc()
        await _store_agent_mem(svc, content="agent decided to deploy", agent_id="a1")
        fake_ing = _FakeIngestor()
        monkeypatch.setattr(_state, "ingestor", fake_ing)

        w = _make_watcher(svc, idle_threshold=10.0, extract_interval=1.0)
        # Simulate a quiet window: last write was 100s ago (>> idle_threshold).
        w.last_write_ts = _iso(100)
        w.last_extract_ts = None  # cadence OK (never extracted)

        res = await w.run_idle_maintenance(agent_id="a1")

        assert res["status"] == "ok"
        assert res["extract"]["extracted"] == 1
        assert len(fake_ing.calls) == 1
        assert fake_ing.calls[0]["origin"] == MemoryOrigin.AGENT
        # item marked extracted
        items = await svc.store_backend.search(
            MemoryFilter(agent_id="a1", origin=MemoryOrigin.AGENT, archived=True)
        )
        assert items[0].metadata.get("extracted") is True

    @pytest.mark.asyncio
    async def test_write_debounces_extract(self, monkeypatch):
        """A fresh write during the poll window → tiers deferred (debounce).

        The watcher bumps last_write_ts to the new MAX and returns
        'deferred'/'write_observed' without extracting.
        """
        svc = _svc()
        await _store_agent_mem(svc, content="agent note", agent_id="a1")
        fake_ing = _FakeIngestor()
        monkeypatch.setattr(_state, "ingestor", fake_ing)

        w = _make_watcher(svc, idle_threshold=10.0, extract_interval=1.0)
        # Set last_write_ts to the OLD max, then store a NEW memory which
        # advances MAX(updated_at) — the watcher must detect the write and
        # defer.
        w.last_write_ts = w._read_max()  # snapshot current max
        # now write something newer
        await _store_agent_mem(svc, content="fresh agent note", agent_id="a1")

        res = await w.run_idle_maintenance(agent_id="a1")
        assert res["status"] == "deferred"
        assert res["reason"] == "write_observed"
        # nothing extracted this round (debounce)
        assert len(fake_ing.calls) == 0

    @pytest.mark.asyncio
    async def test_extract_interval_rate_limits(self, monkeypatch):
        """Even when idle and pending, extract is rate-limited by
        extract_interval: a second sweep right after the first is a noop."""
        svc = _svc()
        await _store_agent_mem(svc, content="agent item", agent_id="a1")
        fake_ing = _FakeIngestor()
        monkeypatch.setattr(_state, "ingestor", fake_ing)

        w = _make_watcher(svc, idle_threshold=10.0, extract_interval=300.0)
        w.last_write_ts = _iso(100)
        # first sweep fires
        r1 = await w.run_idle_maintenance(agent_id="a1")
        assert r1["extract"]["extracted"] == 1
        assert len(fake_ing.calls) == 1
        # second sweep immediately after: cadence NOT elapsed AND no pending
        # (item already marked) → extract does not fire again
        r2 = await w.run_idle_maintenance(agent_id="a1")
        # no new extraction (either cadence-gated or zero pending)
        assert len(fake_ing.calls) == 1

    @pytest.mark.asyncio
    async def test_no_extract_when_not_idle(self, monkeypatch):
        """idle < idle_threshold → extract does not fire even with backlog."""
        svc = _svc()
        await _store_agent_mem(svc, content="agent item", agent_id="a1")
        fake_ing = _FakeIngestor()
        monkeypatch.setattr(_state, "ingestor", fake_ing)

        w = _make_watcher(svc, idle_threshold=60.0, extract_interval=1.0)
        # last write was 5s ago — NOT idle yet
        w.last_write_ts = _iso(5)
        res = await w.run_idle_maintenance(agent_id="a1")
        assert res["status"] == "ok"
        assert len(fake_ing.calls) == 0


class TestIdleTriggerConsolidate:
    @pytest.mark.asyncio
    async def test_consolidate_fires_when_idle(self, monkeypatch):
        svc = _svc()
        emitted: list[Any] = []

        class _FakeBus:
            async def emit(self, evt, ctx):
                emitted.append((evt, ctx))
                return None

        monkeypatch.setattr(_state, "consolidator", object())  # non-None gate
        monkeypatch.setattr(_state, "memory_event_bus", _FakeBus())

        w = _make_watcher(svc, idle_threshold=10.0, consolidate_threshold=10.0)
        w.last_write_ts = _iso(100)  # quiet window
        w.last_consolidate_ts = None
        # ensure extract gate is off (no ingestor) so we isolate consolidate
        monkeypatch.setattr(_state, "ingestor", None)

        res = await w.run_idle_maintenance(agent_id="a1")
        assert res["status"] == "ok"
        assert res["consolidate"]["status"] == "ok"
        assert len(emitted) == 1


# ── D. P0: FOREGROUND never extracted ──────────────────────────────


class TestP0ForegroundProtected:
    @pytest.mark.asyncio
    async def test_foreground_never_extracted_after_idle(self, monkeypatch):
        """A FOREGROUND memory sitting through a long idle window is NEVER
        sent to the IngestorAgent. P0 red-line."""
        svc = _svc()
        await _store_fg_mem(svc, content="user secret note", agent_id="a1")
        fake_ing = _FakeIngestor()
        monkeypatch.setattr(_state, "ingestor", fake_ing)

        w = _make_watcher(svc, idle_threshold=10.0, extract_interval=1.0)
        w.last_write_ts = _iso(1000)  # very long idle window
        w.last_extract_ts = None

        res = await w.run_idle_maintenance(agent_id="a1")
        assert res["status"] == "ok"
        # pending_extract query filters origin=AGENT → 0 pending
        assert res["extract"]["pending"] == 0
        assert res["extract"]["extracted"] == 0
        # the IngestorAgent was never called
        assert len(fake_ing.calls) == 0
        # FOREGROUND item metadata untouched
        items = await svc.store_backend.search(
            MemoryFilter(agent_id="a1", origin=MemoryOrigin.FOREGROUND, archived=True)
        )
        assert items[0].metadata.get("extracted") is None

    @pytest.mark.asyncio
    async def test_pending_count_excludes_foreground(self, monkeypatch):
        """maintenance_snapshot pending_extract counts ONLY origin=AGENT."""
        svc = _svc()
        await _store_fg_mem(svc, content="fg1", agent_id="a1")
        await _store_fg_mem(svc, content="fg2", agent_id="a1")
        await _store_agent_mem(svc, content="ag1", agent_id="a1")
        monkeypatch.setattr(_state, "db_watcher", None)  # snapshot wired check
        w = _make_watcher(svc)
        snap = await w.maintenance_snapshot()
        assert snap["pending_extract"] == 1  # only the AGENT item


# ── C. observability snapshot ──────────────────────────────────────


class TestMaintenanceSnapshot:
    @pytest.mark.asyncio
    async def test_snapshot_reports_clocks(self, monkeypatch):
        svc = _svc()
        await _store_agent_mem(svc, content="ag", agent_id="a1")
        monkeypatch.setattr(_state, "db_watcher", None)
        w = _make_watcher(svc, idle_threshold=30.0, extract_interval=120.0,
                          consolidate_threshold=200.0)
        w.last_write_ts = _iso(50)
        snap = await w.maintenance_snapshot()
        assert "last_write" in snap
        assert "last_deterministic" in snap
        assert "last_extract" in snap
        assert "last_consolidate" in snap
        assert snap["pending_extract"] == 1
        assert snap["idle_seconds"] >= 49.0
        assert snap["thresholds"]["idle"] == 30.0
        assert snap["thresholds"]["extract_interval"] == 120.0
        assert snap["thresholds"]["consolidate"] == 200.0


# ── determinism + re-anchor (regression) ───────────────────────────


class TestReanchorRegression:
    @pytest.mark.asyncio
    async def test_extract_reanchors_watermark(self, monkeypatch):
        """After extract, the watermark advances to post-extract MAX so the
        tiers' own update() writes do not re-trigger indefinitely."""
        svc = _svc()
        await _store_agent_mem(svc, content="agent item", agent_id="a1")
        monkeypatch.setattr(_state, "ingestor", _FakeIngestor())
        w = _make_watcher(svc, idle_threshold=10.0, extract_interval=1.0)
        w.last_write_ts = _iso(100)
        pre_wm = w._last_watermark
        await w.run_idle_maintenance(agent_id="a1")
        post_wm = w._last_watermark
        # watermark advanced (the update() marking extracted bumped updated_at)
        assert _parse_iso(post_wm) >= _parse_iso(pre_wm)
        assert w.last_extract_ts is not None
