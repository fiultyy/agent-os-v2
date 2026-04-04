"""Scope management — session/agent/workspace/global message scopes.

Controls visibility and isolation of messages across four scope levels:
- **agent**: private to a single agent.
- **session**: visible to all agents in the same session.
- **workspace**: visible to all agents in the same workspace/project.
- **global**: visible across all sessions and agents.

Provides scope-based message filtering for cross-domain communication
with trust boundaries.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class ScopeLevel(str, Enum):
    """Scope levels ordered by increasing visibility."""

    AGENT = "agent"
    SESSION = "session"
    WORKSPACE = "workspace"
    GLOBAL = "global"

    @property
    def visibility_rank(self) -> int:
        """Numeric rank for comparison — higher = wider visibility."""
        return {
            ScopeLevel.AGENT: 0,
            ScopeLevel.SESSION: 1,
            ScopeLevel.WORKSPACE: 2,
            ScopeLevel.GLOBAL: 3,
        }[self]

    def includes(self, other: "ScopeLevel") -> bool:
        """Check if this scope level includes another.

        A higher-visibility scope can see messages from lower scopes.
        """
        return self.visibility_rank >= other.visibility_rank


@dataclass
class ScopeMembership:
    """Tracks which scopes an agent belongs to and their metadata."""

    agent_id: str
    scopes: dict[str, ScopeLevel] = field(default_factory=dict)
    # scope_id -> dict of arbitrary metadata (e.g. role, joined_at)
    scope_meta: dict[str, dict[str, Any]] = field(default_factory=dict)


class ScopeManager:
    """Manages communication scopes for agents.

    Tracks which agents belong to which scopes, supports querying
    membership for message routing decisions, and provides scope-based
    message filtering for cross-domain communication.
    """

    def __init__(self) -> None:
        # scope_id -> set of agent_ids
        self._scopes: dict[str, set[str]] = {}
        # scope_id -> ScopeLevel
        self._scope_levels: dict[str, ScopeLevel] = {}
        # agent_id -> ScopeMembership
        self._memberships: dict[str, ScopeMembership] = {}

    # ── Scope lifecycle ──────────────────────────────────────────

    def create_scope(
        self,
        scope_id: str,
        level: ScopeLevel,
        meta: dict[str, Any] | None = None,
    ) -> None:
        """Create a new scope with the given level.

        Args:
            scope_id: Unique scope identifier.
            level: Visibility level for this scope.
            meta: Optional metadata for the scope.
        """
        self._scopes.setdefault(scope_id, set())
        self._scope_levels[scope_id] = level

    def remove_scope(self, scope_id: str) -> None:
        """Remove a scope and unassign all its agents."""
        members = self._scopes.pop(scope_id, set())
        self._scope_levels.pop(scope_id, None)
        for agent_id in members:
            membership = self._memberships.get(agent_id)
            if membership:
                membership.scopes.pop(scope_id, None)
                membership.scope_meta.pop(scope_id, None)

    # ── Membership ───────────────────────────────────────────────

    async def join_scope(
        self,
        agent_id: str,
        scope_id: str,
        level: ScopeLevel | None = None,
        meta: dict[str, Any] | None = None,
    ) -> None:
        """Add an agent to a scope.

        If the scope does not exist, it is created with the given level.

        Args:
            agent_id: Agent to add.
            scope_id: Target scope.
            level: Scope level (required if scope doesn't exist yet).
            meta: Optional per-agent scope metadata.
        """
        if scope_id not in self._scopes:
            if level is None:
                raise ValueError(f"Scope {scope_id!r} does not exist; provide level")
            self.create_scope(scope_id, level)

        self._scopes[scope_id].add(agent_id)

        membership = self._memberships.get(agent_id)
        if membership is None:
            membership = ScopeMembership(agent_id=agent_id)
            self._memberships[agent_id] = membership

        membership.scopes[scope_id] = self._scope_levels[scope_id]
        if meta:
            membership.scope_meta[scope_id] = meta

    async def leave_scope(self, agent_id: str, scope_id: str) -> None:
        """Remove an agent from a scope."""
        members = self._scopes.get(scope_id)
        if members and agent_id in members:
            members.discard(agent_id)

        membership = self._memberships.get(agent_id)
        if membership:
            membership.scopes.pop(scope_id, None)
            membership.scope_meta.pop(scope_id, None)

    # ── Query ────────────────────────────────────────────────────

    async def get_scope(self, agent_id: str) -> dict[str, Any]:
        """Get the current scope info for an agent.

        Returns a dict with agent_id, scope memberships, and member lists.
        """
        membership = self._memberships.get(agent_id)
        if membership is None:
            return {"agent_id": agent_id, "scopes": {}, "members": {}}

        members: dict[str, list[str]] = {}
        for scope_id in membership.scopes:
            members[scope_id] = sorted(self._scopes.get(scope_id, set()))

        return {
            "agent_id": agent_id,
            "scopes": dict(membership.scopes),
            "members": members,
        }

    async def get_members(self, scope_id: str) -> list[str]:
        """Return all agent IDs currently in a scope."""
        return sorted(self._scopes.get(scope_id, set()))

    async def list_scopes(self, level: ScopeLevel | None = None) -> list[str]:
        """Return scope IDs, optionally filtered by level."""
        if level is None:
            return sorted(self._scopes.keys())
        return sorted(
            sid for sid, sl in self._scope_levels.items() if sl == level
        )

    def get_scope_level(self, scope_id: str) -> ScopeLevel | None:
        """Return the level of a scope, or None if not found."""
        return self._scope_levels.get(scope_id)

    # ── Scope-based filtering ────────────────────────────────────

    def can_see(
        self,
        observer_agent_id: str,
        target_scope_id: str,
    ) -> bool:
        """Check whether an agent can see messages in a given scope.

        An agent can see a scope if they are a member of it, or if
        they belong to a higher-visibility scope that encompasses it.
        """
        # Direct membership
        members = self._scopes.get(target_scope_id, set())
        if observer_agent_id in members:
            return True

        # Check if agent belongs to a scope with higher visibility
        target_level = self._scope_levels.get(target_scope_id)
        if target_level is None:
            return False

        membership = self._memberships.get(observer_agent_id)
        if membership is None:
            return False

        for scope_id, scope_level in membership.scopes.items():
            if scope_level.includes(target_level):
                return True

        return False

    def agents_in_scope(self, scope_id: str) -> set[str]:
        """Return the set of agent IDs in a scope (sync version)."""
        return set(self._scopes.get(scope_id, set()))

    def get_agent_scopes(self, agent_id: str) -> dict[str, ScopeLevel]:
        """Return all scope memberships for an agent."""
        membership = self._memberships.get(agent_id)
        if membership is None:
            return {}
        return dict(membership.scopes)
