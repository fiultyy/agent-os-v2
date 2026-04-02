"""Scope management — session/agent/global message scopes.

Controls visibility and isolation of messages:
- session: visible to all agents in the same session
- agent: private to a single agent
- global: visible across all sessions and agents
"""

from typing import Any


class ScopeManager:
    """Manages communication scopes for agents."""

    async def get_scope(self, agent_id: str) -> dict[str, Any]:
        """Get the current scope for an agent."""
        return {"agent_id": agent_id, "scope": "session"}

    async def join_scope(self, agent_id: str, scope_id: str) -> None:
        """Add an agent to a scope."""
        pass

    async def leave_scope(self, agent_id: str, scope_id: str) -> None:
        """Remove an agent from a scope."""
        pass
