"""Graph state definition."""

from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass
class GraphState:
    """State object passed through the graph nodes.

    Encapsulates all runtime data needed for orchestration:
    conversation messages, current node pointer, context, and status.

    Attributes:
        messages: Conversation message history.
        current_node: Name of the currently executing node.
        context: Arbitrary context data shared between nodes.
        status: Execution status (e.g. 'idle', 'running', 'done', 'needs_tool', 'error').
        agent_id: Unique identifier for the agent.
        session_id: Unique identifier for the session.
        input: Current user input.
        output: Latest output from graph execution.
        memory_refs: References to stored memory items.
        tool_results: Results from tool executions.
        errors: Accumulated error messages.
        metadata: Extra metadata.
    """

    messages: list[dict[str, Any]] = field(default_factory=list)
    current_node: str = ""
    context: dict[str, Any] = field(default_factory=dict)
    status: str = "idle"

    agent_id: str = ""
    session_id: str = ""
    input: str = ""
    output: str = ""
    memory_refs: list[str] = field(default_factory=list)
    tool_results: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize state to a plain dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GraphState":
        """Deserialize state from a plain dictionary."""
        return cls(**data)
