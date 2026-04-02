"""Memory type definitions."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class MemoryType(str, Enum):
    """Memory tier classification."""

    WORKING = "working"      # Current conversation context
    EPISODIC = "episodic"    # Past interaction memories
    SEMANTIC = "semantic"    # Knowledge graph / facts
    CORE = "core"            # Persistent agent identity


@dataclass
class MemoryEntry:
    """A single memory entry."""

    id: str = ""
    agent_id: str = ""
    session_id: str = ""
    memory_type: MemoryType = MemoryType.WORKING
    content: str = ""
    embedding: list[float] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    importance: float = 0.5
    created_at: str = ""
    accessed_at: str = ""
