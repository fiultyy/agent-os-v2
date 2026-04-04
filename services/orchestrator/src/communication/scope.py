"""Scope management — session/agent/global message scopes.

Controls visibility and isolation of messages:
- session: visible to all agents in the same session
- agent: private to a single agent
- global: visible across all sessions and agents
"""

from typing import Any


class ScopeManager:
    """Manages communication scopes for agents.

    Tracks which agents belong to which scopes and supports
    querying membership for message routing decisions.
    """

    def __init__(self) -> None:
        # scope_id -> set of agent_ids
        self._scopes: dict[str, set[str]] = {}
        # agent_id -> list of scope_ids
        self._agent_scopes: dict[str, list[str]] = []

    async def get_scope(self, agent_id: str) -> dict[str, Any]:
        """Get the current scope for an agent.

        Returns a dict with agent_id and list of scope memberships.
        """
        scopes = self._agent_scopes.get(agent_id, [])
        members: dict[str, list[str]] = {}
        for scope_id in scopes:
            members[scope_id] = sorted(self._scopes.get(scope_id, set()))
        return {"agent_id": agent_id, "scopes": scopes, "members": members}

    async def join_scope(self, agent_id: str, scope_id: str) -> None:
        """Add an agent to a scope."""
        self._scopes.setdefault(scope_id, set()).add(agent_id)
        scopes = self._agent_scopes.get(agent_id, [])
        if scope_id not in scopes:
            scopes.append(scope_id)
        self._agent_scopes[agent_id] = scopes

    async def leave_scope(self, agent_id: str, scope_id: str) -> None:
        """Remove an agent from a scope."""
        members = self._scopes.get(scope_id)
        if members and agent_id in members:
            members.discard(agent_id)
        scopes = self._agent_scopes.get(agent_id, [])
        if scope_id in scopes:
            scopes.remove(scope_id)

    async def get_members(self, scope_id: str) -> list[str]:
        """Return all agent IDs currently in a scope."""
        return sorted(self._scopes.get(scope_id, set()))

    async def list_scopes(self) -> list[str]:
        """Return all known scope IDs."""
        return sorted(self._scopes.keys())
