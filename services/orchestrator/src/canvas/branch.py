"""P0-5: Branch — divergent timeline in the canvas.

A branch represents a fork in the conversation. The main conversation
is branch "main" (branch_id = "main"). Branches allow exploration,
A/B testing prompts, or parallel tool execution without polluting
the main timeline.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, Optional


class BranchStatus(str, Enum):
    """Lifecycle states for a branch."""
    ACTIVE = "active"       # currently being written to
    MERGED = "merged"       # merged back into parent
    PRUNED = "pruned"       # discarded (but events retained)
    ARCHIVED = "archived"   # long-term storage, read-only


MAIN_BRANCH_ID = "main"


@dataclass
class Branch:
    """A divergent timeline within a canvas session.

    Attributes:
        branch_id: Unique identifier. "main" for the default branch.
        session_id: Parent session this branch belongs to.
        parent_branch_id: The branch this was forked from.
        fork_tick_id: The tick at which the fork happened.
        status: Current lifecycle state.
        created_at: ISO 8601 creation timestamp.
        merged_at: ISO 8601 merge timestamp (if merged).
        metadata: Arbitrary extension data.
    """
    branch_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str = ""
    parent_branch_id: str = MAIN_BRANCH_ID
    fork_tick_id: Optional[str] = None
    status: BranchStatus = BranchStatus.ACTIVE
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    merged_at: Optional[str] = None
    metadata: Dict[str, dict] = field(default_factory=dict)

    @classmethod
    def create_main(cls, session_id: str) -> "Branch":
        """Create the default main branch for a session."""
        return cls(
            branch_id=MAIN_BRANCH_ID,
            session_id=session_id,
            parent_branch_id="",
            status=BranchStatus.ACTIVE,
        )

    @classmethod
    def fork(
        cls,
        session_id: str,
        parent_branch_id: str = MAIN_BRANCH_ID,
        fork_tick_id: str = "",
    ) -> "Branch":
        """Create a new branch forked from an existing branch at a tick."""
        return cls(
            session_id=session_id,
            parent_branch_id=parent_branch_id,
            fork_tick_id=fork_tick_id,
            status=BranchStatus.ACTIVE,
        )

    def to_dict(self) -> dict:
        return {
            "branch_id": self.branch_id,
            "session_id": self.session_id,
            "parent_branch_id": self.parent_branch_id,
            "fork_tick_id": self.fork_tick_id,
            "status": self.status.value,
            "created_at": self.created_at,
            "merged_at": self.merged_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Branch":
        status_val = d.pop("status", "active")
        return cls(
            status=BranchStatus(status_val),
            **{k: v for k, v in d.items() if k in cls.__dataclass_fields__},
        )
