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
        subgraph_results: Results from completed subgraph executions.
        parallel_results: Results from parallel branch executions.
        tool_iteration: Number of tool-execution rounds completed in the
            multi-turn tool_use loop. Incremented by the ``tool`` node and
            checked by the ``llm`` conditional edge against
            ``MAX_TOOL_ITERATIONS`` to force synthesis (anti-infinite-loop).
        tool_use_history: Ordered list of ``{tool_use, tool_result}`` pairs
            accumulated across the loop. Injected back into the LLM messages
            each round so the model sees prior tool calls and results before
            deciding the next step (anthropic tool_use/tool_result sequence).
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
    subgraph_results: dict[str, Any] = field(default_factory=dict)
    parallel_results: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    tool_iteration: int = 0
    tool_use_history: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialize state to a plain dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GraphState":
        """Deserialize state from a plain dictionary."""
        # Handle missing fields gracefully for backward compatibility
        known_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in known_fields}
        return cls(**filtered)

    def clone(self) -> "GraphState":
        """Create an independent deep copy of this state."""
        return GraphState.from_dict(self.to_dict())
