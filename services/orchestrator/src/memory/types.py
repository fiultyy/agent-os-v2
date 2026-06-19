"""Memory type definitions for the Agent OS memory subsystem.

Defines the core data types used across all memory operations:
- :class:`MemoryScope` — trust-domain isolation levels.
- :class:`MemoryType` — four-layer memory tier classification.
- :class:`MemoryOrigin` — provenance (foreground vs agent) [P0].
- :class:`MemoryState` — deterministic lifecycle state [P3].
- :class:`MemoryRef` — lightweight reference to a stored memory.
- :class:`MemoryItem` — a single memory record with content and metadata.
- :class:`MemoryBlock` — fixed-size context block (persona, user profile, etc.).
- :class:`MemoryFilter` — query parameters for memory retrieval.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from datetime import datetime, timezone


class RecallMode(str, Enum):
    """Strategy used by :meth:`MemoryService.recall`."""

    KEYWORD = "keyword"        # Simple case-insensitive keyword matching
    SEMANTIC = "semantic"      # Vector similarity search + optional rerank
    UNIFIED = "unified"        # Keyword + KG dual-path fusion


class MemoryScope(str, Enum):
    """Trust-domain isolation levels for memory access control."""

    AGENT = "agent"          # Per-agent isolated memory
    SESSION = "session"      # Shared within a session/task
    WORKSPACE = "workspace"  # Shared within a project/workspace
    GLOBAL = "global"        # Cross-project universal knowledge


class MemoryType(str, Enum):
    """Four-layer memory tier classification.

    L0: Working — context window, directly available, limited by LLM token budget.
    L1: Session — full session history + state, archived on session end.
    L2: Episodic — cross-session experience fragments with time decay.
    L3: Semantic — persistent knowledge, entity relationships, user profiles.
    """

    WORKING = "working"
    SESSION = "session"
    EPISODIC = "episodic"
    SEMANTIC = "semantic"


class MemoryOrigin(str, Enum):
    """Provenance of a memory item — who created it.

    Determines whether a memory is eligible for autonomous consolidation.

    FOREGROUND: user- or system-directed input (manual entry, prompts,
        tool results a foreground agent was asked to persist). Belongs to
        the user; must never be auto-forgotten / merged / migrated.
    AGENT: agent self-sedimented memory produced by an autonomous
        consolidation path (migrator / reflect / dreamer). Eligible for
        further automatic consolidation.
    """

    FOREGROUND = "foreground"
    AGENT = "agent"


class MemoryState(str, Enum):
    """Deterministic lifecycle state of a memory item [P3].

    Time-driven state machine (managed by TimeBasedStatePruner), decoupled
    from the legacy boolean ``archived`` flag. Only ``origin=AGENT`` items
    transition (FOREGROUND is protected by P0).

    ACTIVE: recently accessed, fully participatable in recall/consolidation.
    STALE: not accessed within ``stale_days`` (default 30) — still recallable
        but flagged for review; pruner will archive it next if still cold.
    ARCHIVED: not accessed within ``archive_days`` (default 90) or below
        archive importance — excluded from regular recall, equivalent to
        ``archived=True`` (kept for backward compat).
    """

    ACTIVE = "active"
    STALE = "stale"
    ARCHIVED = "archived"


@dataclass
class MemoryRef:
    """Lightweight reference to a stored memory item.

    Used as a pointer in graph state and other structures to avoid
    carrying full memory content.

    Attributes:
        id: Unique memory identifier.
        memory_type: Memory tier.
        scope: Trust-domain scope.
    """

    id: str = ""
    memory_type: MemoryType = MemoryType.WORKING
    scope: MemoryScope = MemoryScope.AGENT


@dataclass
class MemoryItem:
    """A single memory record with content and metadata.

    This is the primary data unit stored and retrieved by the memory subsystem.

    Attributes:
        id: Unique memory identifier (UUID).
        agent_id: Agent that owns this memory.
        session_id: Session this memory belongs to.
        memory_type: Memory tier (working/session/episodic/semantic).
        scope: Trust-domain isolation level.
        content: The actual memory content text.
        importance: Computed importance score (0.0–1.0).
        metadata: Arbitrary key-value metadata.
        created_at: ISO-8601 creation timestamp.
        accessed_at: ISO-8601 last access timestamp.
        origin: Who created this memory (foreground vs agent). Foreground
            memories are protected from autonomous consolidation.
        archived: Legacy boolean archive flag (backward compat). Equivalent
            to ``state == ARCHIVED``; kept in sync by the store layer.
        state: Deterministic lifecycle state [P3] (active/stale/archived).
        last_state_transition: ISO-8601 timestamp of the last state change.
    """

    id: str = ""
    agent_id: str = ""
    session_id: str = ""
    memory_type: MemoryType = MemoryType.WORKING
    scope: MemoryScope = MemoryScope.AGENT
    content: str = ""
    importance: float = 0.5
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    accessed_at: str = ""
    origin: MemoryOrigin = MemoryOrigin.FOREGROUND
    archived: bool = False
    state: MemoryState = MemoryState.ACTIVE
    last_state_transition: str = ""

    def __post_init__(self) -> None:
        now = datetime.now(timezone.utc).isoformat()
        if not self.created_at:
            self.created_at = now
        if not self.accessed_at:
            self.accessed_at = now
        # Keep legacy archived flag consistent with state on construction.
        if self.archived and self.state == MemoryState.ACTIVE:
            self.state = MemoryState.ARCHIVED
            if not self.last_state_transition:
                self.last_state_transition = now
        elif self.state == MemoryState.ARCHIVED:
            self.archived = True

    def touch(self) -> None:
        """Update the accessed_at timestamp to now."""
        self.accessed_at = datetime.now(timezone.utc).isoformat()

    def to_ref(self) -> MemoryRef:
        """Create a lightweight reference to this memory item."""
        return MemoryRef(id=self.id, memory_type=self.memory_type, scope=self.scope)


@dataclass
class MemoryBlock:
    """A fixed-size context block attached to an agent.

    Inspired by Letta's core memory blocks. Each agent has a fixed
    set of blocks (e.g. persona, user_profile) with character limits.

    Attributes:
        label: Block identifier (e.g. ``"persona"``, ``"user_profile"``).
        content: Current block content text.
        char_limit: Maximum character count. Writes exceeding this are rejected.
        agent_id: Agent that owns this block.
    """

    label: str = ""
    content: str = ""
    char_limit: int = 2000
    agent_id: str = ""

    @property
    def remaining_chars(self) -> int:
        """Number of characters still available in this block."""
        return max(0, self.char_limit - len(self.content))

    def write(self, text: str) -> None:
        """Write text to the block, replacing all existing content.

        Raises:
            ValueError: If text exceeds ``char_limit``.
        """
        if len(text) > self.char_limit:
            raise ValueError(
                f"Content ({len(text)} chars) exceeds block limit "
                f"({self.char_limit} chars) for block {self.label!r}"
            )
        self.content = text

    def append(self, text: str) -> None:
        """Append text to the existing block content.

        Raises:
            ValueError: If combined content exceeds ``char_limit``.
        """
        combined = self.content + text
        if len(combined) > self.char_limit:
            raise ValueError(
                f"Appended content would exceed block limit "
                f"({self.char_limit} chars) for block {self.label!r}"
            )
        self.content = combined

    def read(self) -> str:
        """Return the current block content."""
        return self.content


@dataclass
class MemoryFilter:
    """Query parameters for memory retrieval operations.

    All fields are optional filters; only items matching all
    specified criteria are returned.

    Attributes:
        agent_id: Filter by owning agent.
        session_id: Filter by session.
        memory_type: Filter by memory tier.
        scope: Filter by trust-domain.
        keyword: Text keyword for content matching.
        min_importance: Minimum importance score threshold.
        origin: Filter by memory provenance (foreground vs agent).
        state: Filter by lifecycle state [P3] (active/stale/archived).
        archived: Include legacy-archived memories (default ``False``).
    """

    agent_id: str = ""
    session_id: str = ""
    memory_type: MemoryType | None = None
    scope: MemoryScope | None = None
    keyword: str = ""
    min_importance: float = 0.0
    origin: MemoryOrigin | None = None
    state: MemoryState | None = None
    archived: bool = False
