"""MemoryDBWatcher — polls for external DB writes and runs deterministic maintenance.

Detects changes to the ``memories`` table made by EXTERNAL processes (other
harnesses sharing the SQLite DB) via an ``updated_at`` watermark, then runs the
deterministic maintenance chain (prune → forget → migrate). **Zero LLM** — all
three stages filter ``origin=AGENT`` only (P0: FOREGROUND / user / external-app
memories are never auto-touched).

Two entry points share the SAME chain:
- the ``_db_watch_loop`` (engine.py) — passive 60s polling
- ``POST /v1/memory/notify`` — active, on-demand

Watermark contract:
- ``_last_watermark`` is initialized in ``__init__`` to ``MAX(updated_at)`` so
  the FIRST poll only fires on writes newer than process start.
- After a successful chain the watermark is RE-READ (post-chain MAX) so the
  chain's own ``update()``/``store()`` writes (which bump ``updated_at``) do
  NOT re-trigger the next poll — avoids a perpetual maintenance loop.
- Comparison is by parsed ``datetime`` (``>``), not string ``!=``, for format
  robustness.
"""

from __future__ import annotations

import asyncio
import logging
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


class MemoryDBWatcher:
    """Polls for external memory DB writes and runs deterministic maintenance.

    Zero LLM. The maintenance chain (prune/forget/migrate) touches only
    ``origin=AGENT`` memories; FOREGROUND (user / external-app-authored)
    memories are P0-protected and never auto-transitioned/archived/merged.
    """

    def __init__(self, memory_service: Any, poll_interval: float = 60.0) -> None:
        self._memory = memory_service
        self._poll_interval = poll_interval
        # Initialize watermark so the first poll only fires on writes newer
        # than process start (do not reprocess pre-existing rows on boot).
        self._last_watermark: str | None = self._read_max()
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
                    if emit:
                        _state.emit_memory_event("prune", {
                            "agent_id": agent_id, "trigger": trigger,
                            "scanned": pr.scanned, "stale": pr.to_stale,
                            "archived": pr.to_archived,
                        })
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
                    if emit:
                        _state.emit_memory_event("forget", {
                            "agent_id": agent_id, "trigger": trigger,
                            "scanned": fr.scanned, "archived": fr.archived,
                            "archived_ids": fr.archived_ids[:10],
                        })
                except Exception as exc:
                    logger.exception("watcher forget failed for %s", agent_id)
                    self.last_error = f"forget: {exc}"
            # (3) episodic → semantic migration
            if _state.memory_migrator is not None:
                try:
                    migrate_ids = await _state.memory_migrator.migrate_episodic_to_semantic(agent_id)
                    result["migrated"] = len(migrate_ids)
                    if emit:
                        _state.emit_memory_event("migrate", {
                            "agent_id": agent_id, "trigger": trigger,
                            "path": "episodic_to_semantic",
                            "count": len(migrate_ids),
                            "ids": migrate_ids[:10],
                        })
                except Exception as exc:
                    logger.exception("watcher migrate failed for %s", agent_id)
                    self.last_error = f"migrate: {exc}"
            # Re-anchor watermark to POST-chain MAX so the chain's own writes
            # (which bump updated_at) do NOT re-trigger the next poll.
            post = self._read_max()
            if post:
                self._last_watermark = post
            self.last_run = datetime.now(timezone.utc).isoformat()
            result["status"] = "ok"
            return result

    async def run_once_all(self, *, emit: bool = True, trigger: str = "poll") -> list[dict[str, Any]]:
        """Run maintenance for every known agent (snapshots ``_state.agents``)."""
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
