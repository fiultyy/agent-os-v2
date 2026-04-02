"""Graph edges and conditional routing."""

from typing import Any, Callable


class ConditionalEdge:
    """A conditional edge that routes state to different nodes.

    Used to implement branching logic in the orchestration graph,
    e.g. tool-call routing, error handling, loop detection.
    """

    def __init__(
        self,
        source: str,
        targets: dict[str, str],
        condition: Callable[[dict], str] | None = None,
    ):
        self.source = source
        self.targets = targets  # {condition_result: target_node_name}
        self.condition = condition

    def route(self, state: dict) -> str:
        """Evaluate condition and return the target node name."""
        if self.condition:
            result = self.condition(state)
            return self.targets.get(result, self.targets.get("__default__", ""))
        return self.targets.get("__default__", "")
