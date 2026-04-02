"""Graph node definitions."""

from typing import Any, Callable


class GraphNode:
    """A node in the orchestration graph.

    Each node represents a discrete processing step:
    - LLM call
    - Tool execution
    - Context compilation
    - Memory retrieval
    """

    def __init__(self, name: str, handler: Callable[..., Any] | None = None):
        self.name = name
        self.handler = handler

    async def execute(self, state: dict) -> dict:
        """Execute this node with the given state, return updated state."""
        if self.handler:
            return await self.handler(state)
        return state
