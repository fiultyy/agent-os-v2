"""Graph edges and conditional routing.

Provides ``Edge`` for unconditional transitions and
``ConditionalEdge`` for branching based on graph state fields.
"""

from typing import Callable

from src.graph.state import GraphState


class Edge:
    """An unconditional edge from one node to another.

    Attributes:
        source: Name of the source node.
        target: Name of the target node.
    """

    def __init__(self, source: str, target: str) -> None:
        self.source = source
        self.target = target

    def route(self, state: GraphState) -> str:
        """Always return the fixed target node name."""
        return self.target

    def __repr__(self) -> str:
        return f"Edge({self.source!r} -> {self.target!r})"


class ConditionalEdge:
    """A conditional edge that routes based on a state field value.

    By default routes on ``state.status``. For custom routing logic,
    provide a sync ``condition`` callable that receives state and
    returns a key into ``targets``.

    Attributes:
        source: Name of the source node.
        targets: Mapping from field value (or condition result) to target node name.
            Use ``__default__`` key as fallback.
        condition_field: State attribute name to inspect (default ``"status"``).
        condition: Optional sync callable ``GraphState -> str`` overriding field lookup.
    """

    def __init__(
        self,
        source: str,
        targets: dict[str, str],
        condition_field: str = "status",
        condition: Callable[[GraphState], str] | None = None,
    ) -> None:
        self.source = source
        self.targets = targets
        self.condition_field = condition_field
        self.condition = condition

    def route(self, state: GraphState) -> str:
        """Evaluate state and return the target node name."""
        if self.condition is not None:
            result = self.condition(state)
        else:
            value = getattr(state, self.condition_field, "")
            result = str(value)

        return self.targets.get(result, self.targets.get("__default__", ""))

    def __repr__(self) -> str:
        return f"ConditionalEdge({self.source!r} -> {list(self.targets.keys())})"
