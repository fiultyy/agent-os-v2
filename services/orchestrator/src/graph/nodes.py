"""Graph node definitions.

Provides the abstract base class for all graph nodes and
concrete implementations for common node types.
"""

from abc import ABC, abstractmethod
from typing import Any, Callable, Awaitable

from src.graph.state import GraphState


class GraphNode(ABC):
    """Abstract base class for a node in the orchestration graph.

    Each node represents a discrete processing step. Subclasses must
    implement the ``execute`` method which receives the current graph
    state and returns an updated state.
    """

    def __init__(self, name: str) -> None:
        self.name = name

    @abstractmethod
    async def execute(self, state: GraphState) -> GraphState:
        """Execute this node with the given state, return updated state."""

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name={self.name!r})"


class FunctionNode(GraphNode):
    """A node that wraps an async function.

    Convenience class for quickly creating nodes from callable handlers.
    """

    def __init__(
        self,
        name: str,
        handler: Callable[[GraphState], Awaitable[GraphState]],
    ) -> None:
        super().__init__(name)
        self._handler = handler

    async def execute(self, state: GraphState) -> GraphState:
        """Delegate execution to the wrapped handler function."""
        return await self._handler(state)
