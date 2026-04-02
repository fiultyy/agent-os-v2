"""Memory store — persistence layer for agent memories.

Supports pluggable backends:
- In-memory (development)
- Vector store (semantic search)
- Knowledge graph (entity relationships)
- File-based (simple persistence)
"""

from typing import Any

from src.memory.types import MemoryEntry, MemoryType


class MemoryStore:
    """Base memory store with in-memory implementation."""

    def __init__(self) -> None:
        self._entries: dict[str, MemoryEntry] = {}

    async def put(self, entry: MemoryEntry) -> str:
        """Store an entry, return its ID."""
        self._entries[entry.id] = entry
        return entry.id

    async def search(
        self,
        query: str,
        memory_type: MemoryType | None = None,
        top_k: int = 10,
    ) -> list[MemoryEntry]:
        """Search for relevant entries."""
        results = list(self._entries.values())
        if memory_type:
            results = [e for e in results if e.memory_type == memory_type]
        return results[:top_k]

    async def delete(self, entry_id: str) -> None:
        """Delete an entry by ID."""
        self._entries.pop(entry_id, None)
