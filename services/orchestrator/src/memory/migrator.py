"""MemoryMigrator — orchestrates four-layer memory migration.

Migration paths:
- L0 Working → L1 Session: Auto batch write after node execution.
- L1 Session → L2 Episodic: Async buffer at session end, periodic flush.
- L2 Episodic → L3 Semantic: Periodic entity/relation extraction.

Each migration step is independently callable and idempotent.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from src.memory.types import MemoryItem, MemoryType, MemoryScope, MemoryFilter, MemoryOrigin
from src.memory.service import MemoryService
from src.memory.scorer import ImportanceScorer

logger = logging.getLogger(__name__)


# ── Migration helpers ───────────────────────────────────────────────

def _extract_entities(text: str) -> list[str]:
    """Extract simple named entities from text.

    Heuristic: capitalized multi-word phrases and quoted strings.
    """
    entities: list[str] = []

    # Capitalized phrases (2+ words starting with uppercase)
    for match in re.finditer(r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\b', text):
        entities.append(match.group(1))

    # Quoted strings
    for match in re.finditer(r'"([^"]+)"', text):
        val = match.group(1).strip()
        if len(val) > 2:
            entities.append(val)

    return entities[:10]  # Cap at 10 entities


def _extract_relations(text: str) -> list[dict[str, str]]:
    """Extract simple subject-predicate-object triples from text.

    Heuristic: look for patterns like "X is Y", "X uses Y", "X depends on Y".
    """
    relations: list[dict[str, str]] = []
    patterns = [
        (r'(\w+)\s+(?:is|are|was|were)\s+(.+?)(?:\.|,|$)', "is_a"),
        (r'(\w+)\s+(?:uses?|utilizes?)\s+(.+?)(?:\.|,|$)', "uses"),
        (r'(\w+)\s+(?:depends?\s+on|requires?)\s+(.+?)(?:\.|,|$)', "depends_on"),
        (r'(\w+)\s+(?:contains?|has?)\s+(.+?)(?:\.|,|$)', "contains"),
    ]

    for pattern, rel_type in patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            subject = match.group(1).strip()
            obj = match.group(2).strip()
            if len(subject) > 1 and len(obj) > 1:
                relations.append({
                    "subject": subject,
                    "predicate": rel_type,
                    "object": obj,
                })

    return relations[:10]


# ── Migrator classes ────────────────────────────────────────────────

class WorkingToSessionMigrator:
    """Migrates L0 Working memory → L1 Session memory.

    Triggered after each node execution. Batch-writes working
    items into session storage with importance scoring.
    """

    def __init__(
        self,
        memory_service: MemoryService,
        scorer: ImportanceScorer | None = None,
    ) -> None:
        self._memory = memory_service
        self._scorer = scorer or ImportanceScorer()
        self._buffer: list[MemoryItem] = []

    async def enqueue(self, item: MemoryItem) -> None:
        """Buffer a working memory item for migration."""
        item.memory_type = MemoryType.WORKING
        self._buffer.append(item)

    async def flush(self, session_id: str, agent_id: str) -> list[str]:
        """Flush all buffered working items to session memory.

        Returns:
            List of new session memory IDs.
        """
        if not self._buffer:
            return []

        ids: list[str] = []
        for item in self._buffer:
            scored = self._scorer.score(item)
            ref = await self._memory.store(
                content=item.content,
                agent_id=agent_id,
                session_id=session_id,
                memory_type=MemoryType.SESSION,
                scope=item.scope,
                importance=scored.total,
                origin=MemoryOrigin.AGENT,
                metadata={
                    **item.metadata,
                    "migrated_from": "working",
                    "migration_time": datetime.now(timezone.utc).isoformat(),
                    "score_breakdown": {
                        "recency": scored.recency,
                        "frequency": scored.frequency,
                        "relevance": scored.relevance,
                        "emotional_weight": scored.emotional_weight,
                        "actionability": scored.actionability,
                    },
                },
            )
            ids.append(ref.id)

        count = len(self._buffer)
        self._buffer.clear()
        logger.info("Migrated %d working items → session %s", count, session_id)
        return ids


class SessionToEpisodicMigrator:
    """Migrates L1 Session memory → L2 Episodic memory.

    Triggered at session end. Buffers session memories, then
    compresses them into episodic fragments with key entity extraction.
    """

    def __init__(self, memory_service: MemoryService) -> None:
        self._memory = memory_service
        self._buffer: list[MemoryItem] = []

    def buffer_session_items(self, items: list[MemoryItem]) -> None:
        """Buffer session items for episodic migration."""
        self._buffer.extend(items)

    async def migrate(self, agent_id: str) -> list[str]:
        """Compress buffered session items into episodic memories.

        Creates episodic fragments by grouping related session items
        and extracting key entities and timestamps.

        Returns:
            List of new episodic memory IDs.
        """
        if not self._buffer:
            return []

        # Group by time windows (hourly)
        groups = self._group_by_time_window(self._buffer)

        ids: list[str] = []
        for window, items in groups.items():
            # Extract key information
            all_content = "\n".join(i.content for i in items)
            entities = _extract_entities(all_content)

            summary = self._generate_episode_summary(items, entities)

            ref = await self._memory.store(
                content=summary,
                agent_id=agent_id,
                memory_type=MemoryType.EPISODIC,
                scope=MemoryScope.AGENT,
                importance=max(i.importance for i in items),
                origin=MemoryOrigin.AGENT,
                metadata={
                    "migrated_from": "session",
                    "source_count": len(items),
                    "time_window": window,
                    "entities": entities,
                    "session_ids": list({i.session_id for i in items}),
                    "migration_time": datetime.now(timezone.utc).isoformat(),
                },
            )
            ids.append(ref.id)

        count = len(self._buffer)
        self._buffer.clear()
        logger.info("Migrated %d session items → %d episodic memories", count, len(ids))
        return ids

    def _group_by_time_window(
        self, items: list[MemoryItem],
    ) -> dict[str, list[MemoryItem]]:
        """Group items by hourly time window."""
        groups: dict[str, list[MemoryItem]] = {}
        for item in items:
            try:
                dt = datetime.fromisoformat(item.created_at)
                window = dt.strftime("%Y-%m-%dT%H")
            except (ValueError, TypeError):
                window = "unknown"
            groups.setdefault(window, []).append(item)
        return groups

    def _generate_episode_summary(
        self, items: list[MemoryItem], entities: list[str],
    ) -> str:
        """Generate a summary for an episode."""
        key_points = []
        for item in items[:5]:  # Top 5 most important
            key_points.append(item.content[:100])

        entity_str = ", ".join(entities[:5]) if entities else "N/A"
        return (
            f"[Episode] {len(items)} interactions. "
            f"Key entities: {entity_str}. "
            f"Highlights: {'; '.join(key_points)}"
        )


class EpisodicToSemanticMigrator:
    """Migrates L2 Episodic memory → L3 Semantic memory.

    Triggered periodically. Extracts entities and relationships
    from episodic memories into persistent semantic knowledge.
    """

    def __init__(self, memory_service: MemoryService) -> None:
        self._memory = memory_service

    async def migrate(
        self,
        agent_id: str,
        episodic_items: list[MemoryItem],
    ) -> list[str]:
        """Extract entities and relations from episodic memories into semantic.

        Returns:
            List of new semantic memory IDs.
        """
        if not episodic_items:
            return []

        ids: list[str] = []
        for item in episodic_items:
            entities = _extract_entities(item.content)
            relations = _extract_relations(item.content)

            if not entities and not relations:
                continue

            # Create semantic memory for each entity
            for entity in entities:
                entity_content = f"[Entity] {entity}"
                # Check if this entity already exists
                existing = await self._memory.recall(
                    query=entity,
                    agent_id=agent_id,
                    memory_type=MemoryType.SEMANTIC,
                    top_k=1,
                )
                if existing and any(entity in e.content for e in existing):
                    # Update existing entity with new episode reference
                    continue

                ref = await self._memory.store(
                    content=entity_content,
                    agent_id=agent_id,
                    memory_type=MemoryType.SEMANTIC,
                    scope=MemoryScope.AGENT,
                    importance=item.importance,
                    origin=MemoryOrigin.AGENT,
                    metadata={
                        "migrated_from": "episodic",
                        "source_episode": item.id,
                        "type": "entity",
                        "migration_time": datetime.now(timezone.utc).isoformat(),
                    },
                )
                ids.append(ref.id)

            # Create semantic memory for relations
            if relations:
                rel_strs = [
                    f"{r['subject']} {r['predicate']} {r['object']}"
                    for r in relations
                ]
                ref = await self._memory.store(
                    content=f"[Relations] {'; '.join(rel_strs)}",
                    agent_id=agent_id,
                    memory_type=MemoryType.SEMANTIC,
                    scope=MemoryScope.AGENT,
                    importance=item.importance * 0.9,
                    origin=MemoryOrigin.AGENT,
                    metadata={
                        "migrated_from": "episodic",
                        "source_episode": item.id,
                        "type": "relation",
                        "relations": relations,
                        "migration_time": datetime.now(timezone.utc).isoformat(),
                    },
                )
                ids.append(ref.id)

        logger.info(
            "Migrated %d episodic items → %d semantic memories",
            len(episodic_items), len(ids),
        )
        return ids


class MemoryMigrator:
    """Orchestrator for four-layer memory migration.

    Coordinates all three migration paths and provides a unified
    interface for the memory lifecycle.
    """

    def __init__(self, memory_service: MemoryService) -> None:
        self._memory = memory_service
        self._w2s = WorkingToSessionMigrator(memory_service)
        self._s2e = SessionToEpisodicMigrator(memory_service)
        self._e2sem = EpisodicToSemanticMigrator(memory_service)

    @property
    def working_to_session(self) -> WorkingToSessionMigrator:
        return self._w2s

    @property
    def session_to_episodic(self) -> SessionToEpisodicMigrator:
        return self._s2e

    @property
    def episodic_to_semantic(self) -> EpisodicToSemanticMigrator:
        return self._e2sem

    async def migrate_working_to_session(
        self, item: MemoryItem, session_id: str, agent_id: str,
    ) -> str | None:
        """Enqueue a working item and immediately flush."""
        await self._w2s.enqueue(item)
        ids = await self._w2s.flush(session_id, agent_id)
        return ids[0] if ids else None

    async def migrate_session_to_episodic(
        self, session_id: str, agent_id: str,
    ) -> list[str]:
        """Migrate all session items to episodic memory."""
        # P0 provenance: only migrate agent-self-sedimented session
        # memories; FOREGROUND (user-entered) memories are left untouched.
        f = MemoryFilter(
            session_id=session_id,
            memory_type=MemoryType.SESSION,
            origin=MemoryOrigin.AGENT,
        )
        items = await self._memory._store.search(f)
        self._s2e.buffer_session_items(items)
        return await self._s2e.migrate(agent_id)

    async def migrate_episodic_to_semantic(
        self, agent_id: str,
    ) -> list[str]:
        """Migrate episodic memories to semantic knowledge."""
        # P0 provenance: only promote agent-self-sedimented episodic
        # memories to semantic; FOREGROUND (user-entered) are left alone.
        f = MemoryFilter(
            agent_id=agent_id,
            memory_type=MemoryType.EPISODIC,
            origin=MemoryOrigin.AGENT,
        )
        items = await self._memory._store.search(f)
        return await self._e2sem.migrate(agent_id, items)
