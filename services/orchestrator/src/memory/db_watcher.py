"""MemoryDBWatcher — polls for external DB writes and runs tiered idle maintenance.

Detects changes to the ``memories`` table made by EXTERNAL processes (other
harnesses sharing the SQLite DB) via an ``updated_at`` watermark, then runs a
TIERED maintenance schedule gated on write-idle time:

  (1) deterministic chain (prune → forget → migrate): zero LLM, runs whenever
      the DB advanced past the watermark (existing behaviour, preserved). Only
      touches ``origin=AGENT`` memories; FOREGROUND is P0-protected.
  (2) extract (① IngestorAgent, glm-4-flash side channel): runs when the DB has
      been write-idle ≥ ``MEMORY_IDLE_EXTRACT_INTERVAL`` AND the per-agent
      extract cadence (``MEMORY_IDLE_EXTRACT_INTERVAL``) has elapsed AND there
      are ``origin=AGENT`` memories not yet extracted (``metadata.extracted``
      unset). Each ingested memory is marked ``metadata.extracted=True`` to
      avoid re-extraction. Re-uses the per-agent ``asyncio.Lock``.
  (3) consolidate (② ConsolidatorAgent via ``EventType.CONSOLIDATE``): runs
      when write-idle ≥ ``MEMORY_IDLE_CONSOLIDATE_THRESHOLD``.

Idle debounce: a new external write (MAX(updated_at) advanced) RESETS the
write-idle clock so a burst of writes defers (2)/(3) to the NEXT quiet window
("wait for the next idle") — only the zero-cost deterministic chain keeps
running. (1) is the only tier that runs while writes are still flowing.

Watermark contract:
- ``_last_watermark`` is initialized in ``__init__`` to ``MAX(updated_at)`` so
  the FIRST poll only fires on writes newer than process start.
- After a successful chain / extract / consolidate the watermark is RE-READ
  (post-stage MAX) so the stage's own ``update()``/``store()`` writes (which
  bump ``updated_at``) do NOT re-trigger the next poll — avoids a perpetual
  maintenance loop.

P0 red-line (decisive): FOREGROUND / user / external-app-authored memories are
NEVER extracted or consolidated. The pending-extract query filters
``origin=AGENT`` AND the per-item extract re-checks origin defensively.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


def _parse_iso(ts: str) -> datetime:
    """Parse an ISO-8601 timestamp (tolerant of a trailing ``Z``).

    On failure returns ``datetime.min`` (tz-aware) so an unparseable external
    timestamp does NOT fire maintenance on garbage.
    """
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return datetime.min.replace(tzinfo=timezone.utc)


def _now_ts() -> str:
    """Current UTC time as an ISO-8601 string (tz-aware)."""
    return datetime.now(timezone.utc).isoformat()


def _latest_ts(per_agent: dict[str, str]) -> str | None:
    """Latest (max) timestamp across a per-agent cadence dict.

    Used to flatten the per-agent ``last_extract_ts`` / ``last_consolidate_ts``
    dicts back into a single scalar for the /debug/status snapshot (the consumer
    in entities.py expects a scalar ``last_extract`` / ``last_consolidate`` field,
    not a dict). Returns ``None`` when no agent has run the tier yet.
    """
    vals = [v for v in per_agent.values() if v]
    if not vals:
        return None
    return max(vals, key=_parse_iso)


class MemoryDBWatcher:
    """Polls for external memory DB writes and runs tiered idle maintenance.

    Tier (1) deterministic chain is zero-LLM and touches only ``origin=AGENT``
    memories. Tiers (2) extract and (3) consolidate are gated on write-idle
    time and likewise never touch FOREGROUND memories (P0-protected).
    """

    def __init__(self, memory_service: Any, poll_interval: float = 60.0) -> None:
        self._memory = memory_service
        self._poll_interval = poll_interval
        # Initialize watermark so the first poll only fires on writes newer
        # than process start (do not reprocess pre-existing rows on boot).
        self._last_watermark: str | None = self._read_max()

        # ── idle-trigger thresholds (env-configurable, safe defaults) ──
        # Deterministic chain runs whenever the watermark advanced (no idle
        # gate) — this is the existing zero-cost behaviour.
        self.idle_threshold: float = float(
            os.getenv("MEMORY_IDLE_THRESHOLD", "60")
        )
        # Extract cadence: minimum seconds between extract sweeps.
        self.extract_interval: float = float(
            os.getenv("MEMORY_IDLE_EXTRACT_INTERVAL", "300")
        )
        # Consolidate cadence: minimum seconds between consolidate sweeps.
        self.consolidate_threshold: float = float(
            os.getenv("MEMORY_IDLE_CONSOLIDATE_THRESHOLD", "300")
        )
        # Max items extracted per idle sweep. Bounds the duration a single
        # agent holds its per-agent Lock during a serial LLM batch, so the
        # deterministic chain (prune/forget/migrate, sharing the same lock) is
        # not blocked for minutes when an agent has thousands of backlogged
        # origin=AGENT items. Remainder is digested on subsequent sweeps.
        self.extract_batch_limit: int = int(
            os.getenv("MEMORY_IDLE_EXTRACT_BATCH_LIMIT", "50")
        )

        # ── idle / cadence clocks (ISO-8601 UTC, None = never yet) ──
        # last_write_ts: most-recent external write observed by the watcher
        # (debounce anchor for tiers 2/3). Bootstrapped to the current MAX so
        # pre-existing rows do not count as "a fresh write". This is a GLOBAL
        # clock (single watcher, one debounce signal for the whole process) —
        # it is NOT per-agent.
        self.last_write_ts: str | None = self._last_watermark or _now_ts()
        self.last_deterministic_ts: str | None = None
        # extract / consolidate cadence clocks are PER-AGENT: keyed by agent_id,
        # value is the ISO timestamp of that agent's last sweep. ``run_idle_once_all``
        # iterates agents sequentially, so a single global cadence value would let
        # the first agent's sweep set the timestamp and immediately short-circuit
        # every subsequent agent's cadence check (multi-agent starvation bug).
        # Empty dict = "never extracted/consolidated for any agent".
        self.last_extract_ts: dict[str, str] = {}
        self.last_consolidate_ts: dict[str, str] = {}

        self._locks: dict[str, asyncio.Lock] = {}
        self.last_run: str | None = None
        self.last_error: str | None = None

    # ── Watermark / change detection ───────────────────────────────

    def _read_max(self) -> str | None:
        """Read current ``MAX(updated_at)`` via the Store's public method."""
        store = getattr(self._memory, "store_backend", None)
        if store is None or not hasattr(store, "max_updated_at"):
            return None
        try:
            return store.max_updated_at()
        except Exception as exc:  # never crash the watcher on a store error
            logger.warning("MemoryDBWatcher read max failed: %s", exc)
            return None

    def has_external_changes(self) -> str | None:
        """Return the new ``MAX(updated_at)`` if the DB advanced past the watermark.

        Returns ``None`` when there is nothing new (steady state) or the store
        is not a SQL backend. The returned value is the PRE-chain max; the
        watermark itself is advanced post-chain inside :meth:`run_maintenance`.
        """
        current = self._read_max()
        if not current:
            return None
        if self._last_watermark is None:
            return current
        if _parse_iso(current) > _parse_iso(self._last_watermark):
            return current
        return None

    @property
    def poll_interval(self) -> float:
        """Polling cadence in seconds."""
        return self._poll_interval

    def bump_write_clock(self, new_max: str | None) -> None:
        """Reset the write-idle clock and watermark to ``new_max``.

        Called by the engine's ``_watch_loop`` whenever an external write is
        detected (``has_external_changes`` returned a value). This is the
        production debounce anchor: a fresh write RESETS ``last_write_ts`` so
        tiers 2/3 are deferred to the NEXT quiet window, and advances the
        watermark so the same write is not re-detected next poll.

        Without this call ``last_write_ts`` would be frozen at the boot
        bootstrap value and ``_idle_seconds()`` would grow monotonically —
        the debounce would be defeated (tiers would fire mid-burst instead of
        waiting for a lull).
        """
        if not new_max:
            return
        self.last_write_ts = new_max
        self._last_watermark = new_max

    def _idle_seconds(self) -> float:
        """Seconds since the most recent observed write (debounce clock).

        ``last_write_ts`` is bumped whenever the watcher observes a watermark
        advance (``has_external_changes`` detected a new write). When the
        clock has not yet been set we fall back to the watermark bootstrap. A
        large value means "quiet window" — tiers 2/3 may fire.
        """
        anchor = self.last_write_ts or self._last_watermark
        if not anchor:
            return 0.0
        try:
            delta = datetime.now(timezone.utc) - _parse_iso(anchor)
        except Exception:
            return 0.0
        return max(0.0, delta.total_seconds())

    async def maintenance_snapshot(self) -> dict[str, Any]:
        """Read-only snapshot of the maintenance clocks (for /debug/status).

        Async because the pending-extract count awaits ``store.search``.
        """
        from src.services import _state  # late import (wired at engine startup)
        pending = 0
        try:
            pending = await self._count_pending_extract()
        except Exception:
            pending = 0
        return {
            "last_write": self.last_write_ts,
            "idle_seconds": round(self._idle_seconds(), 1),
            "last_deterministic": self.last_deterministic_ts,
            # last_extract / last_consolidate are per-agent dicts internally;
            # flatten to the max across agents so the snapshot keeps its scalar
            # shape (backward-compatible with /debug/status consumers).
            "last_extract": _latest_ts(self.last_extract_ts),
            "last_consolidate": _latest_ts(self.last_consolidate_ts),
            "pending_extract": pending,
            "thresholds": {
                "idle": self.idle_threshold,
                "extract_interval": self.extract_interval,
                "consolidate": self.consolidate_threshold,
            },
            # surface whether the watcher is wired (engine sets _state.db_watcher)
            "wired": _state.db_watcher is not None,
        }

    # ── Maintenance ────────────────────────────────────────────────

    def _lock_for(self, agent_id: str) -> asyncio.Lock:
        key = agent_id or "__all__"
        if key not in self._locks:
            self._locks[key] = asyncio.Lock()
        return self._locks[key]

    async def run_maintenance(
        self,
        agent_id: str = "",
        *,
        emit: bool = True,
        force: bool = False,
        trigger: str = "poll",
    ) -> dict[str, Any]:
        """Run the deterministic chain (prune → forget → migrate) for one agent.

        Never raises — every stage is individually guarded. Uses a per-agent
        ``asyncio.Lock`` so ``/notify`` and the 60s loop cannot overlap for the
        same agent (prevents duplicate semantic memories from racing migrate).
        If maintenance is already running for this agent, returns
        ``{'status': 'skipped', 'reason': 'in_progress'}``.

        This is tier (1): zero-LLM, runs on every detected change regardless of
        write-idle (cheap). Tiers (2) extract and (3) consolidate are gated on
        idle time and run from :meth:`run_idle_maintenance`.
        """
        lock = self._lock_for(agent_id)
        if lock.locked():
            return {"status": "skipped", "reason": "in_progress", "agent_id": agent_id}

        from src.services import _state  # read at call time (engine wires at import)

        async with lock:
            result: dict[str, Any] = {
                "agent_id": agent_id,
                "trigger": trigger,
                "pruned": None,
                "forgot": None,
                "migrated": None,
            }
            # (1) deterministic state pruning (ACTIVE → STALE → ARCHIVED)
            if _state.state_pruner is not None:
                try:
                    pr = await _state.state_pruner.prune(agent_id=agent_id)
                    result["pruned"] = {
                        "scanned": pr.scanned,
                        "stale": pr.to_stale,
                        "archived": pr.to_archived,
                    }
                    if emit and _state.memory_observe_emitter is not None:
                        from src.memory.observe_hook import memory_event
                        await _state.memory_observe_emitter.emit(memory_event("prune", {
                            "agent_id": agent_id, "trigger": trigger,
                            "scanned": pr.scanned, "stale": pr.to_stale,
                            "archived": pr.to_archived,
                        }))
                except Exception as exc:
                    logger.exception("watcher prune failed for %s", agent_id)
                    self.last_error = f"prune: {exc}"
            # (2) importance-based forgetting
            if _state.active_forgetting is not None:
                try:
                    fr = await _state.active_forgetting.run_sweep(agent_id=agent_id)
                    result["forgot"] = {
                        "scanned": fr.scanned,
                        "archived": fr.archived,
                    }
                    if emit and _state.memory_observe_emitter is not None:
                        from src.memory.observe_hook import memory_event
                        await _state.memory_observe_emitter.emit(memory_event("forget", {
                            "agent_id": agent_id, "trigger": trigger,
                            "scanned": fr.scanned, "archived": fr.archived,
                            "archived_ids": fr.archived_ids[:10],
                        }))
                except Exception as exc:
                    logger.exception("watcher forget failed for %s", agent_id)
                    self.last_error = f"forget: {exc}"
            # (3) episodic → semantic migration
            if _state.memory_migrator is not None:
                try:
                    migrate_ids = await _state.memory_migrator.migrate_episodic_to_semantic(agent_id)
                    result["migrated"] = len(migrate_ids)
                    if emit and _state.memory_observe_emitter is not None:
                        from src.memory.observe_hook import memory_event
                        await _state.memory_observe_emitter.emit(memory_event("migrate", {
                            "agent_id": agent_id, "trigger": trigger,
                            "path": "episodic_to_semantic",
                            "count": len(migrate_ids),
                            "ids": migrate_ids[:10],
                        }))
                except Exception as exc:
                    logger.exception("watcher migrate failed for %s", agent_id)
                    self.last_error = f"migrate: {exc}"
            # Re-anchor watermark to POST-chain MAX so the chain's own writes
            # (which bump updated_at) do NOT re-trigger the next poll.
            post = self._read_max()
            if post:
                self._last_watermark = post
            self.last_deterministic_ts = _now_ts()
            self.last_run = self.last_deterministic_ts
            result["status"] = "ok"
            return result

    async def run_idle_maintenance(
        self,
        agent_id: str = "",
        *,
        trigger: str = "idle_poll",
    ) -> dict[str, Any]:
        """Run the LLM-bearing idle tiers (extract + consolidate) for one agent.

        Gated on write-idle time (debounced: a fresh write resets the clock so
        a burst of writes defers these tiers to the NEXT quiet window). Uses
        the SAME per-agent ``asyncio.Lock`` as the deterministic chain so the
        tiers never overlap for one agent.

        Tier (2) extract: idle ≥ ``idle_threshold`` AND the PER-AGENT cadence
        ``now - last_extract_ts[agent_id] ≥ extract_interval`` AND pending-extract
        count > 0 → batch-call IngestorAgent.ingest (P0: only origin=AGENT) and mark
        each ingested item ``metadata.extracted=True``.
        Tier (3) consolidate: idle ≥ ``consolidate_threshold`` → emit
        ``EventType.CONSOLIDATE`` (ConsolidatorAgent).

        Returns a dict describing which tiers fired. Never raises — every tier
        is individually guarded. If the agent's lock is held, returns skipped.
        """
        lock = self._lock_for(agent_id)
        if lock.locked():
            return {"status": "skipped", "reason": "in_progress", "agent_id": agent_id}

        from src.services import _state  # late import (engine wires at startup)

        result: dict[str, Any] = {
            "agent_id": agent_id,
            "trigger": trigger,
            "extract": None,
            "consolidate": None,
        }

        # ── debounce: a fresh write defers the LLM tiers ──────────────
        # The debounce signal is the SAME watermark-advance test the
        # deterministic poll uses (``has_external_changes``): if MAX(updated_at)
        # advanced past ``_last_watermark`` since the last poll, a real write
        # landed this window → reset the idle clock and skip tiers 2/3 this
        # round (only the zero-cost deterministic chain keeps running via
        # run_once_all / run_maintenance). This is the "wait for the next
        # idle" debounce. Using the watermark (not a raw MAX comparison)
        # correctly distinguishes "a write arrived" from "an old write still
        # happens to be the MAX".
        new_max = self.has_external_changes()
        if new_max is not None:
            # A write landed during this poll window → not idle. Reset the
            # idle clock and re-anchor the watermark so the next poll does not
            # re-detect the same write.
            self.last_write_ts = new_max
            self._last_watermark = new_max
            result["status"] = "deferred"
            result["reason"] = "write_observed"
            return result

        idle = self._idle_seconds()
        now = datetime.now(timezone.utc)

        async with lock:
            # ── tier (2): extract ──────────────────────────────────────
            # Gate: idle ≥ idle_threshold (quiet window) AND per-agent extract
            # cadence elapsed AND there is un-extracted AGENT backlog.
            # Cadence is PER-AGENT: read this agent's last-sweep ts. A global
            # cadence value would let the first agent's sweep short-circuit the
            # rest when run_idle_once_all iterates them in sequence.
            last_ex = self.last_extract_ts.get(agent_id)
            cadence_ok = (
                last_ex is None
                or (now - _parse_iso(last_ex)).total_seconds()
                >= self.extract_interval
            )
            if (
                idle >= self.idle_threshold
                and cadence_ok
                and _state.ingestor is not None
            ):
                try:
                    extracted = await self._run_extract(agent_id)
                    result["extract"] = extracted
                    self.last_extract_ts[agent_id] = _now_ts()
                except Exception as exc:
                    logger.exception("watcher extract failed for %s", agent_id)
                    self.last_error = f"extract: {exc}"
                    result["extract"] = {"status": "error", "error": str(exc)}

            # ── tier (3): consolidate ──────────────────────────────────
            # Gate: idle ≥ consolidate_threshold AND per-agent consolidate
            # cadence elapsed. Emits EventType.CONSOLIDATE → ConsolidatorHook.
            # Per-agent for the same multi-agent starvation reason as extract.
            last_consol = self.last_consolidate_ts.get(agent_id)
            consol_cadence_ok = (
                last_consol is None
                or (now - _parse_iso(last_consol)).total_seconds()
                >= self.consolidate_threshold
            )
            if (
                idle >= self.consolidate_threshold
                and consol_cadence_ok
                and _state.consolidator is not None
                and _state.memory_event_bus is not None
            ):
                try:
                    from src.memory.event_bus import EventType
                    from src.memory.hooks import ConsolidateContext
                    ctx = ConsolidateContext(
                        agent_id=agent_id,
                        session_id="",
                        trigger="idle_poll",
                        top_k=20,
                    )
                    await _state.memory_event_bus.emit(EventType.CONSOLIDATE, ctx)
                    self.last_consolidate_ts[agent_id] = _now_ts()
                    result["consolidate"] = {"status": "ok", "trigger": "idle_poll"}
                except Exception as exc:
                    logger.exception("watcher consolidate failed for %s", agent_id)
                    self.last_error = f"consolidate: {exc}"
                    result["consolidate"] = {"status": "error", "error": str(exc)}

            # Re-anchor post-stage MAX so the tiers' own writes do not
            # re-trigger the next poll.
            post = self._read_max()
            if post:
                self._last_watermark = post

        result["status"] = "ok"
        return result

    # ── extract helpers ────────────────────────────────────────────

    async def _pending_extract_items(
        self, agent_id: str, *, limit: int | None = None
    ) -> list[Any]:
        """Return ``origin=AGENT`` memories not yet extracted, for one agent.

        P0 decisive: ONLY ``origin=AGENT`` items qualify — FOREGROUND is
        excluded at the query layer (MemoryFilter.origin). An item is
        "pending" when ``metadata.extracted`` is not truthy. Uses the store's
        public ``search(MemoryFilter)`` so both InMemoryStore and SQLiteStore
        share one path.

        ``limit`` caps the returned list (post-filter) so a single idle sweep
        cannot run away into a multi-minute serial LLM batch over thousands of
        backlogged items — the remainder is digested on subsequent sweeps.
        ``None`` (default) returns everything; callers needing an exact count
        (``_count_pending_extract``) pass ``None``.
        """
        from src.memory.types import MemoryOrigin, MemoryFilter
        store = getattr(self._memory, "store_backend", None)
        if store is None or not hasattr(store, "search"):
            return []
        flt = MemoryFilter(
            agent_id=agent_id or "",
            origin=MemoryOrigin.AGENT,
            # include both active and archived-then-revived; the deterministic
            # chain owns archiving — extract is orthogonal provenance work.
            archived=True,
        )
        try:
            items = await store.search(flt)
        except Exception:
            return []
        out = []
        for it in items:
            meta = getattr(it, "metadata", None) or {}
            if not meta.get("extracted"):
                out.append(it)
                if limit is not None and len(out) >= limit:
                    break
        return out

    async def _count_pending_extract(self, agent_id: str = "") -> int:
        """Count of un-extracted ``origin=AGENT`` memories (cross-agent when
        ``agent_id`` is empty). Best-effort; returns 0 on any store error.
        """
        try:
            return len(await self._pending_extract_items(agent_id))
        except Exception:
            return 0

    async def _run_extract(self, agent_id: str) -> dict[str, Any]:
        """Batch-extract pending ``origin=AGENT`` memories for one agent.

        Calls ``_state.ingestor.ingest(...)`` (① IngestorAgent, P0 re-checked
        per item) and marks each processed item ``metadata.extracted=True`` so
        it is not re-extracted. Re-anchors the watermark after the batch.
        """
        from src.services import _state
        from src.memory.types import MemoryOrigin

        # Cap the batch so a single sweep cannot hold the per-agent Lock for
        # minutes over thousands of backlogged items (the deterministic chain
        # shares this lock). The full pending count is reported separately for
        # observability; remaining items are digested on subsequent sweeps.
        batch = await self._pending_extract_items(agent_id, limit=self.extract_batch_limit)
        items = batch
        summary: dict[str, Any] = {
            "status": "ok",
            "pending": len(items),
            "extracted": 0,
            "skipped": 0,
            "errors": 0,
        }
        if not items or _state.ingestor is None:
            return summary

        for it in items:
            # P0 defensive re-check: even though the query filters AGENT, an
            # in-flight state change could have flipped origin. Never extract
            # FOREGROUND.
            if getattr(it, "origin", MemoryOrigin.FOREGROUND) == MemoryOrigin.FOREGROUND:
                summary["skipped"] += 1
                continue
            content = getattr(it, "content", "") or ""
            try:
                res = await _state.ingestor.ingest(
                    memory_id=it.id,
                    content=content,
                    agent_id=getattr(it, "agent_id", agent_id) or agent_id,
                    session_id=getattr(it, "session_id", "") or "",
                    origin=MemoryOrigin.AGENT,
                )
                triggered = bool(getattr(res, "triggered", False))
                skipped = bool(getattr(res, "skipped", False))
            except Exception as exc:
                logger.warning("watcher extract item %s failed: %s", it.id, exc)
                summary["errors"] += 1
                continue
            # Mark extracted regardless of triggered/skipped so a permanent
            # failure / no-KG skip does not hot-loop every idle window. The
            # mark is the dedupe fence; re-extraction needs an explicit clear.
            #
            # IMPORTANT: re-read the LATEST item before merging metadata. The
            # `it` snapshot was taken BEFORE ingest; IngestorAgent.ingest just
            # wrote ``metadata.identity_category`` / ``metadata.degraded`` to
            # the same row. On the SQLite backend ``store.search`` returns
            # deserialized copies (a stale pre-ingest snapshot), and
            # ``store.update`` whole-replaces the metadata column — so using
            # the stale snapshot would silently clobber identity_category.
            # Re-getting picks up the post-ingest state, then we merge our
            # ``extracted`` marker on top instead of overwriting.
            try:
                latest = await self._memory.get(it.id)
                base_meta = getattr(latest, "metadata", None) if latest else None
                meta = dict(base_meta or getattr(it, "metadata", None) or {})
                meta["extracted"] = True
                if triggered:
                    meta["extracted_triggered"] = True
                elif skipped:
                    meta["extracted_skipped"] = True
                await self._memory.update(it.id, metadata=meta)
            except Exception as exc:
                logger.warning("watcher mark extracted %s failed: %s", it.id, exc)
                summary["errors"] += 1
                continue
            if triggered:
                summary["extracted"] += 1
            else:
                summary["skipped"] += 1
        return summary

    async def run_once_all(self, *, emit: bool = True, trigger: str = "poll") -> list[dict[str, Any]]:
        """Run the deterministic chain for every known agent (snapshots ``_state.agents``)."""
        from src.services import _state
        agent_ids = list(_state.agents.keys())
        results: list[dict[str, Any]] = []
        for aid in agent_ids:
            try:
                r = await self.run_maintenance(agent_id=aid, emit=emit, trigger=trigger)
                results.append(r)
            except Exception as exc:
                logger.exception("watcher run_once_all agent %s failed", aid)
                results.append({"agent_id": aid, "status": "error", "error": str(exc)})
        return results

    async def run_idle_once_all(self, *, trigger: str = "idle_poll") -> list[dict[str, Any]]:
        """Run the idle tiers (extract + consolidate) for every known agent.

        Also includes agents that own pending-extract backlog but are NOT
        registered in ``_state.agents`` (e.g. an external harness mirrored an
        agent's dialogue via ``POST /memories?origin=agent`` with an
        unregistered ``agent_id``). Without this, those orphan ``origin=AGENT``
        memories would surface in ``pending_extract`` but never digest — the
        observability panel and actual digestion would diverge.
        """
        from src.services import _state
        agent_ids = set(_state.agents.keys())
        # Union with agent_ids owning un-extracted origin=AGENT backlog so
        # orphan (unregistered) agents are still digested. Best-effort: a store
        # error simply falls back to registered agents only.
        try:
            pending = await self._pending_extract_items(agent_id="", limit=None)
            for it in pending:
                aid = getattr(it, "agent_id", "") or ""
                if aid:
                    agent_ids.add(aid)
        except Exception:
            pass
        results: list[dict[str, Any]] = []
        for aid in agent_ids:
            try:
                r = await self.run_idle_maintenance(agent_id=aid, trigger=trigger)
                results.append(r)
            except Exception as exc:
                logger.exception("watcher run_idle_once_all agent %s failed", aid)
                results.append({"agent_id": aid, "status": "error", "error": str(exc)})
        return results
