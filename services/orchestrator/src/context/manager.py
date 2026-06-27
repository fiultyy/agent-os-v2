"""ContextManager — select context operation.

NOTE: write/compress/isolate/get_isolated_context removed in Phase 2 L6
dead-code cleanup (no production callers; context write/compress now handled
by the memory subsystem). Only ``select`` (context recall bridge) remains.
"""

from typing import Any

from src.memory import MemoryService


class ContextManager:
    """Manages context lifecycle across the orchestration graph.

    Coordinates between the working context (what's in the current
    conversation window) and the external memory store (MemoryService).
    """

    def __init__(self, memory_service: MemoryService) -> None:
        self._memory = memory_service

    async def select(
        self,
        session_id: str,
        query: str,
        agent_id: str = "",
        top_k: int = 10,
    ) -> list[dict[str, Any]]:
        """Select relevant context entries from external memory.

        Args:
            session_id: Current session.
            query: Search keywords.
            agent_id: Owning agent.
            top_k: Max results.

        Returns:
            List of context dicts with content and metadata.
        """
        items = await self._memory.recall(
            query=query,
            agent_id=agent_id,
            session_id=session_id,
            top_k=top_k,
        )
        return [
            {
                "content": item.content,
                "importance": item.importance,
                "memory_id": item.id,
                "created_at": item.created_at,
            }
            for item in items
        ]
