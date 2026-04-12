"""Session lifecycle management."""

from typing import Any

from src.memory.store import InMemoryStore
from src.memory.types import MemoryItem, MemoryRef, MemoryType

from ._blocks import BlockOperations


class SessionOperations:
    """Manages session creation, archival, destruction, and consolidation."""

    def __init__(
        self,
        store: InMemoryStore,
        blocks: BlockOperations,
        store_func,  # Callable: the CrudOperations.store method
    ) -> None:
        self._store = store
        self._blocks = blocks
        self._store_func = store_func

    async def create_session(self, session_id: str, agent_id: str) -> dict[str, Any]:
        """Create a new session and initialize agent blocks if needed."""
        blocks = await self._store.list_blocks(agent_id)
        if not blocks:
            await self._blocks.init_agent_blocks(agent_id)
        return await self._store.create_session(session_id, agent_id)

    async def get_recent(self, session_id: str, limit: int = 5) -> list[MemoryItem]:
        """Get the most recent memory items for a session."""
        return await self._store.get_recent(session_id, limit=limit)

    async def archive_session(self, session_id: str) -> bool:
        """Archive a session and all its memories."""
        return await self._store.archive_session(session_id)

    async def destroy_session(self, session_id: str) -> bool:
        """Destroy a session and all its memory items."""
        return await self._store.destroy_session(session_id)

    async def reflect(
        self,
        agent_id: str,
        trigger: str = "periodic",
        top_k: int = 20,
        recall_func=None,
    ) -> list[MemoryRef]:
        """Consolidate recent episodic memories into semantic knowledge.

        Retrieves the most recent EPISODIC memories for *agent_id*,
        inspects them for duplicates and contradictions, then merges
        related items into new SEMANTIC memories.

        Args:
            agent_id: The agent whose memories to consolidate.
            trigger: Reason for the consolidation.
            top_k: How many recent episodic items to consider.
            recall_func: Callable for the recall operation.

        Returns:
            A list of :class:`MemoryRef` for the newly created semantic
            memories.
        """
        episodic_items: list[MemoryItem] = await recall_func(
            query="",
            agent_id=agent_id,
            memory_type=MemoryType.EPISODIC,
            top_k=top_k,
        )

        if not episodic_items:
            return []

        groups: list[list[MemoryItem]] = []
        used_ids: set[str] = set()

        for item in episodic_items:
            if item.id in used_ids:
                continue
            group = [item]
            used_ids.add(item.id)
            words = set(item.content.lower().split())

            for other in episodic_items:
                if other.id in used_ids:
                    continue
                other_words = set(other.content.lower().split())
                if words and other_words:
                    overlap = len(words & other_words) / len(words | other_words)
                    if overlap >= 0.4:
                        group.append(other)
                        used_ids.add(other.id)

            groups.append(group)

        new_refs: list[MemoryRef] = []

        for group in groups:
            if len(group) < 2:
                continue

            seen_fragments: set[str] = set()
            merged_parts: list[str] = []
            for member in group:
                fragment = member.content.strip()
                if fragment not in seen_fragments:
                    seen_fragments.add(fragment)
                    merged_parts.append(fragment)

            merged_content = "\n---\n".join(merged_parts)

            source_ids = [m.id for m in group]
            max_importance = max(m.importance for m in group)

            ref = await self._store_func(
                content=merged_content,
                agent_id=agent_id,
                memory_type=MemoryType.SEMANTIC,
                importance=max_importance,
                metadata={
                    "consolidation_trigger": trigger,
                    "source_ids": source_ids,
                    "merged_count": len(group),
                },
            )
            new_refs.append(ref)

        return new_refs
