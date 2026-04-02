"""Graph state definition."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class GraphState:
    """State object passed through the graph nodes.

    Encapsulates all runtime data needed for orchestration:
    conversation context, memory references, tool results, etc.
    """

    agent_id: str = ""
    session_id: str = ""
    input: str = ""
    output: str = ""
    context: dict[str, Any] = field(default_factory=dict)
    memory_refs: list[str] = field(default_factory=list)
    tool_results: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
