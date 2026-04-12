"""Abstract base class for recall strategies."""

from abc import ABC, abstractmethod

from src.memory.types import MemoryItem, MemoryType, MemoryScope


class RecallStrategy(ABC):
    """Base class for memory recall strategies.

    Each concrete strategy implements a different retrieval
    algorithm (keyword, semantic, KG, shared).
    """

    @abstractmethod
    async def recall(
        self,
        query: str,
        agent_id: str,
        session_id: str,
        memory_type: MemoryType | None,
        scope: MemoryScope | None,
        top_k: int,
    ) -> list[MemoryItem]:
        """Execute the recall strategy and return matching memory items."""
