"""MemoryService — manages agent memory across sessions.

Supports multiple memory tiers:
- Working memory (current conversation context)
- Episodic memory (past interactions)
- Semantic memory (knowledge graph)
- Core memory (persistent agent identity/instructions)
"""

from typing import Any

from src.memory.types import MemoryEntry, MemoryType
from src.memory.store import MemoryStore


class MemoryService:
    """High-level memory management for agents."""

    def __init__(self, store: MemoryStore | None = None):
        self.store = store or MemoryStore()

    async def store(self, entry: MemoryEntry) -> str:
        """Store a memory entry, return its ID."""
        return await self.store.put(entry)

    async def retrieve(
        self, query: str, memory_type: MemoryType | None = None, top_k: int = 10
    ) -> list[MemoryEntry]:
        """Retrieve relevant memories."""
        return await self.store.search(query, memory_type=memory_type, top_k=top_k)

    async def forget(self, entry_id: str) -> None:
        """Delete a memory entry (forgetting)."""
        await self.store.delete(entry_id)
