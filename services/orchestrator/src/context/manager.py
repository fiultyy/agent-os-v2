"""ContextManager — write/select/compress/isolate context operations.

Implements the four fundamental context operations:
- write: add new context entries
- select: retrieve relevant context for the current task
- compress: summarize or truncate context to fit budget
- isolate: create isolated context scopes for sub-agents
"""

import copy
import uuid
from typing import Any

from src.memory import MemoryService, MemoryType, MemoryScope


class ContextManager:
    """Manages context lifecycle across the orchestration graph.

    Coordinates between the working context (what's in the current
    conversation window) and the external memory store (MemoryService).
    """

    def __init__(self, memory_service: MemoryService) -> None:
        self._memory = memory_service
        self._isolated_scopes: dict[str, list[dict[str, Any]]] = {}

    async def write(
        self,
        session_id: str,
        entry: dict[str, Any],
        agent_id: str = "",
    ) -> None:
        """Write a context entry to external memory.

        Offloads information that is no longer in the immediate focus
        to the MemoryService so the working context stays lean.

        Args:
            session_id: Current session.
            entry: Context data to externalize.
            agent_id: Owning agent.
        """
        content = entry.get("content", str(entry))
        await self._memory.store(
            content=content,
            agent_id=agent_id,
            session_id=session_id,
            memory_type=MemoryType.SESSION,
            scope=MemoryScope.AGENT,
            importance=entry.get("importance", 0.5),
            metadata={"source": "context_write", **entry.get("metadata", {})},
        )

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

    async def compress(
        self,
        entries: list[dict[str, Any]],
        target_tokens: int,
    ) -> list[dict[str, Any]]:
        """Compress context entries to fit a token budget.

        MVP strategy: simple truncation that preserves entries with
        high importance scores and recent entries.

        Args:
            entries: Context entries to compress.
            target_tokens: Desired upper bound for total tokens.

        Returns:
            Compressed list of entries fitting within budget.
        """
        if not entries:
            return []

        # Rough token estimate: ~4 chars per token
        def _est_tokens(entry: dict[str, Any]) -> int:
            return max(1, len(entry.get("content", "")) // 4)

        # Sort by importance descending, then by recency
        sorted_entries = sorted(
            entries,
            key=lambda e: (e.get("importance", 0.5), e.get("created_at", "")),
            reverse=True,
        )

        result: list[dict[str, Any]] = []
        total = 0
        for entry in sorted_entries:
            tokens = _est_tokens(entry)
            if total + tokens > target_tokens:
                continue
            result.append(entry)
            total += tokens

        return result

    async def isolate(self, parent_session_id: str) -> str:
        """Create an isolated context scope for a sub-agent.

        The isolated scope starts with a copy of the parent's context
        but mutations in the child do not affect the parent.

        Args:
            parent_session_id: Parent session to fork from.

        Returns:
            New isolated scope ID.
        """
        scope_id = f"{parent_session_id}:iso:{uuid.uuid4().hex[:8]}"
        # Copy parent context into isolated scope
        parent_items = await self._memory.recall(
            query="",
            session_id=parent_session_id,
            top_k=50,
        )
        self._isolated_scopes[scope_id] = [
            {"content": item.content, "importance": item.importance}
            for item in parent_items
        ]
        return scope_id

    def get_isolated_context(self, scope_id: str) -> list[dict[str, Any]]:
        """Read the context entries in an isolated scope."""
        return copy.deepcopy(self._isolated_scopes.get(scope_id, []))
