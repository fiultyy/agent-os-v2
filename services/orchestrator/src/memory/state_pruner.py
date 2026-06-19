"""TimeBasedStatePruner — deterministic memory lifecycle transitions [P3].

Time-driven state machine for memory items: ACTIVE → STALE → ARCHIVED.
Runs BEFORE LLM consolidation in the engine sweep loop, so cold / low-value
items are pruned at zero LLM cost (deterministic膨胀控制).

Only ``origin=AGENT`` memories transition (P0 protects FOREGROUND). Hermes
has no curator to copy from — this is a fresh, conservative implementation
(initial 30/90-day thresholds, tunable at runtime).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from src.memory.service import MemoryService
from src.memory.types import MemoryFilter, MemoryOrigin, MemoryState

logger = logging.getLogger(__name__)


@dataclass
class StatePrunerConfig:
    """Thresholds for time-based state transitions.

    Conservative defaults; tune at runtime (start lenient, tighten over
    time). ``archive_importance`` lets high-value items survive even when
    cold (importance scored by the five-dim scorer, not replaced).
    """

    stale_days: float = 30.0
    archive_days: float = 90.0
    archive_importance: float = 0.1
    batch_size: int = 200


@dataclass
class PruneResult:
    """Outcome of a prune pass."""

    scanned: int = 0
    to_stale: int = 0
    to_archived: int = 0
    stale_ids: list[str] = field(default_factory=list)
    archived_ids: list[str] = field(default_factory=list)


class TimeBasedStatePruner:
    """Deterministic, time-driven memory state transitions.

    Scans ``origin=AGENT`` ACTIVE memories; transitions by age/importance:
    - not accessed within ``archive_days`` (default 90) OR importance below
      ``archive_importance`` → ARCHIVED.
    - otherwise not accessed within ``stale_days`` (default 30) → STALE.
    Each transition writes ``state`` + ``last_state_transition`` (and syncs
    the legacy ``archived`` flag for backward compat).
    """

    def __init__(
        self,
        memory_service: MemoryService,
        config: StatePrunerConfig | None = None,
    ) -> None:
        self._memory = memory_service
        self._config = config or StatePrunerConfig()

    async def prune(self, agent_id: str = "") -> PruneResult:
        """Transition ACTIVE agent memories by age/importance.

        Args:
            agent_id: Restrict to one agent; empty string = all agents.

        Returns:
            :class:`PruneResult` with transition counts.
        """
        result = PruneResult()
        f = MemoryFilter(
            agent_id=agent_id,
            origin=MemoryOrigin.AGENT,
            state=MemoryState.ACTIVE,
        )
        items = await self._memory._store.search(f)
        result.scanned = len(items)

        now_dt = datetime.now(timezone.utc)
        now_iso = now_dt.isoformat()

        for item in items:
            age_days = self._age_days(item.accessed_at, now_dt)
            target: MemoryState | None = None
            if (
                age_days >= self._config.archive_days
                or item.importance < self._config.archive_importance
            ):
                target = MemoryState.ARCHIVED
            elif age_days >= self._config.stale_days:
                target = MemoryState.STALE

            if target is None:
                continue

            try:
                await self._memory.update(
                    item.id,
                    state=target,
                    last_state_transition=now_iso,
                    archived=(target == MemoryState.ARCHIVED),
                )
            except Exception:
                logger.warning(
                    "pruner: failed to transition %s", item.id, exc_info=True
                )
                continue

            if target == MemoryState.STALE:
                result.to_stale += 1
                result.stale_ids.append(item.id)
            else:
                result.to_archived += 1
                result.archived_ids.append(item.id)

        logger.info(
            "State prune: scanned=%d stale=%d archived=%d",
            result.scanned,
            result.to_stale,
            result.to_archived,
        )
        return result

    @staticmethod
    def _age_days(accessed_at: str, now_dt: datetime) -> float:
        """Age in days since last access (0 on parse failure)."""
        try:
            accessed = datetime.fromisoformat(accessed_at)
            return max(0.0, (now_dt - accessed).total_seconds() / 86400)
        except (ValueError, TypeError):
            return 0.0
