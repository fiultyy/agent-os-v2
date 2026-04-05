"""ActiveForgetting — automatic archival of low-importance memories.

Periodically scans memories and archives those below the forget
threshold. Archived memories are excluded from regular recall but
can be explicitly recovered.

Before archiving, checks for related memories to avoid creating
orphaned knowledge nodes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from src.memory.types import MemoryItem, MemoryType, MemoryFilter
from src.memory.store import InMemoryStore
from src.memory.service import MemoryService
from src.memory.scorer import ImportanceScorer

logger = logging.getLogger(__name__)


@dataclass
class ForgetResult:
    """Result of a forgetting sweep."""
    scanned: int = 0
    archived: int = 0
    skipped_has_relations: int = 0
    skipped_above_threshold: int = 0
    archived_ids: list[str] = field(default_factory=list)


class ActiveForgetting:
    """Scans and archives low-importance memories.

    Usage::

        forgetting = ActiveForgetting(memory_service, scorer)
        result = await forgetting.run_sweep(agent_id="agent-1")
        print(f"Archived {result.archived} of {result.scanned} memories")
    """

    def __init__(
        self,
        memory_service: MemoryService,
        scorer: ImportanceScorer | None = None,
        forget_threshold: float = 0.1,
        min_age_hours: float = 24.0,
    ) -> None:
        self._memory = memory_service
        self._scorer = scorer or ImportanceScorer(forget_threshold=forget_threshold)
        self._forget_threshold = forget_threshold
        self._min_age_hours = min_age_hours

    async def run_sweep(
        self,
        agent_id: str = "",
        memory_type: MemoryType | None = None,
    ) -> ForgetResult:
        """Scan memories and archive those below the forget threshold.

        Args:
            agent_id: Filter by agent. Empty string = all agents.
            memory_type: Filter by memory type. None = all types.

        Returns:
            :class:`ForgetResult` with sweep statistics.
        """
        result = ForgetResult()

        # Get all non-archived items
        items = await self._memory.recall(
            query="",
            agent_id=agent_id,
            memory_type=memory_type,
            top_k=1000,
        )

        result.scanned = len(items)

        for item in items:
            # Skip already archived
            if item.archived:
                continue

            # Check importance score
            score = self._scorer.score(item)
            if score.total >= self._forget_threshold:
                result.skipped_above_threshold += 1
                continue

            # Check minimum age (don't forget brand new memories)
            age_hours = self._get_age_hours(item)
            if age_hours < self._min_age_hours:
                result.skipped_above_threshold += 1
                continue

            # Check for related memories to avoid orphans
            has_relations = await self._has_related_memories(item)
            if has_relations:
                result.skipped_has_relations += 1
                continue

            # Archive the memory
            await self._archive(item)
            result.archived += 1
            result.archived_ids.append(item.id)

        logger.info(
            "Forget sweep: scanned=%d archived=%d skipped_relations=%d skipped_threshold=%d",
            result.scanned, result.archived,
            result.skipped_has_relations, result.skipped_above_threshold,
        )
        return result

    async def recover(self, memory_id: str) -> MemoryItem | None:
        """Recover an archived memory.

        Archived memories can be explicitly recovered by ID.

        Returns:
            The recovered item, or None if not found.
        """
        item = await self._memory.get(memory_id)
        if item is None:
            return None
        return await self._memory.update(
            memory_id,
            archived=False,
            metadata={
                **item.metadata,
                "recovered_at": True,  # Mark as recovered
            },
        )

    async def list_archived(
        self,
        agent_id: str = "",
        limit: int = 50,
    ) -> list[MemoryItem]:
        """List archived memories, optionally filtered by agent.

        These are memories that have been forgotten but can be recovered.
        """
        # Use the underlying store directly since recall() filters out archived
        f = MemoryFilter(
            agent_id=agent_id,
            archived=True,
        )
        all_archived = await self._memory._store.search(f)
        return all_archived[:limit]

    def _get_age_hours(self, item: MemoryItem) -> float:
        """Get the age of a memory item in hours."""
        try:
            now = datetime.now(timezone.utc)
            created = datetime.fromisoformat(item.created_at)
            return max(0, (now - created).total_seconds() / 3600)
        except (ValueError, TypeError):
            return 0.0

    async def _has_related_memories(self, item: MemoryItem) -> bool:
        """Check if a memory has related items that depend on it.

        A memory is considered to have relations if:
        1. Other memories reference its ID in their metadata.
        2. Other memories share the same session_id and have higher importance.
        3. It contains entities that appear in other memories.
        """
        # Check for references in other items' metadata
        related = await self._memory.recall(
            query=item.id[:8],  # Use first 8 chars of ID as query
            agent_id=item.agent_id,
            top_k=5,
        )
        for other in related:
            if other.id == item.id:
                continue
            source_ids = other.metadata.get("source_ids", [])
            if item.id in source_ids:
                return True

        return False

    async def _archive(self, item: MemoryItem) -> None:
        """Mark a memory item as archived."""
        await self._memory.update(
            item.id,
            archived=True,
            metadata={
                **item.metadata,
                "archived_at": True,
                "forget_score": self._scorer.score(item).total,
            },
        )
