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


class LLMNode(GraphNode):
    """Simulated LLM call node.

    In MVP mode the LLM call is mocked — it echoes the input
    with a prefix. Replace the body with a real LLM adapter
    when available.
    """

    async def execute(self, state: GraphState) -> GraphState:
        """Simulate an LLM response based on user input."""
        user_input = state.input
        response = f"[LLM] Processed: {user_input}"

        state.messages.append({"role": "user", "content": user_input})
        state.messages.append({"role": "assistant", "content": response})
        state.output = response
        state.current_node = self.name
        return state


class ToolCallNode(GraphNode):
    """Simulated tool-call node.

    Resolves tool name from state.context["tool_call"] and
    records the result.
    """

    async def execute(self, state: GraphState) -> GraphState:
        """Simulate a tool invocation."""
        tool_call = state.context.get("tool_call", "unknown_tool")
        result = f"[Tool] {tool_call} executed successfully"

        state.tool_results.append({"tool": tool_call, "result": result})
        state.context["tool_result"] = result
        state.current_node = self.name
        return state
