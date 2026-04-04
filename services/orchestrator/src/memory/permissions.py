"""Memory permissions — 5-level access control for cross-agent memory sharing.

Permission levels:
- **L0 (None)**: No access — the agent cannot even see the memory exists.
- **L1 (Metadata)**: Can see that a memory exists and its metadata, but not content.
- **L2 (Summary)**: Can read an auto-generated summary of the content.
- **L3 (Full)**: Can read the full content.
- **L4 (Admin)**: Can read, update, and delete.

Default cross-domain access is L2 (summary-only). Agents within the same
trust domain get L3 (full). Explicit grants can elevate or restrict access.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import IntEnum
from typing import Any

logger = logging.getLogger(__name__)

# Action constants
ACTION_READ = "read"
ACTION_WRITE = "write"
ACTION_DELETE = "delete"
ACTION_GRANT = "grant"
ACTION_REVOKE = "revoke"


class PermissionLevel(IntEnum):
    """Memory access permission levels."""

    NONE = 0         # No access
    METADATA = 1     # See metadata only
    SUMMARY = 2      # Read auto-generated summary
    FULL = 3         # Read full content
    ADMIN = 4        # Read, update, delete


@dataclass
class AccessGrant:
    """An explicit permission grant from one agent to another.

    Attributes:
        id: Unique grant ID.
        grantor_id: Agent granting the permission.
        grantee_id: Agent receiving the permission.
        target_agent_id: Agent whose memories are being shared.
        level: Permission level granted.
        memory_type_filter: Optional filter restricting to specific memory types.
        expires_at: Optional ISO-8601 expiry timestamp.
        created_at: Creation timestamp.
    """

    id: str = ""
    grantor_id: str = ""
    grantee_id: str = ""
    target_agent_id: str = ""
    level: PermissionLevel = PermissionLevel.SUMMARY
    memory_type_filter: str | None = None
    expires_at: str | None = None
    created_at: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            self.id = str(uuid.uuid4())
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()

    @property
    def is_expired(self) -> bool:
        """Check if the grant has expired."""
        if not self.expires_at:
            return False
        try:
            expiry = datetime.fromisoformat(self.expires_at)
            return datetime.now(timezone.utc) > expiry
        except (ValueError, TypeError):
            return False


@dataclass
class AccessLogEntry:
    """Log entry for a memory access event."""

    id: str = ""
    accessor_id: str = ""
    target_agent_id: str = ""
    memory_id: str = ""
    action: str = ACTION_READ
    level_granted: PermissionLevel = PermissionLevel.NONE
    granted_at: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            self.id = str(uuid.uuid4())
        if not self.granted_at:
            self.granted_at = datetime.now(timezone.utc).isoformat()


def _generate_summary(content: str, max_length: int = 120) -> str:
    """Generate a truncated summary from full content.

    Args:
        content: Full content text.
        max_length: Maximum summary length.

    Returns:
        Truncated summary with ellipsis if needed.
    """
    if len(content) <= max_length:
        return content
    truncated = content[:max_length]
    last_period = truncated.rfind(".")
    last_space = truncated.rfind(" ")
    break_point = last_period if last_period > max_length // 2 else last_space
    if break_point > 0:
        return content[:break_point + 1] + "..."
    return truncated + "..."


class PermissionManager:
    """Manages memory access permissions between agents.

    Default behavior:
    - Same agent: L4 (admin) access to its own memories.
    - Same trust domain: L3 (full) access.
    - Cross-domain: L2 (summary) access by default.
    - No grant: L0 (none) for sensitive memories.

    Explicit grants via :meth:`grant` can override defaults.
    """

    def __init__(self) -> None:
        self._grants: dict[str, AccessGrant] = {}
        # (grantee_id, target_agent_id) -> list of AccessGrant
        self._grants_index: dict[tuple[str, str], list[AccessGrant]] = {}
        self._access_log: list[AccessLogEntry] = []

    # ── Grant management ──────────────────────────────────────────

    def grant(
        self,
        grantor_id: str,
        grantee_id: str,
        target_agent_id: str,
        level: PermissionLevel,
        memory_type_filter: str | None = None,
        expires_at: str | None = None,
    ) -> AccessGrant:
        """Create an explicit permission grant.

        Args:
            grantor_id: Agent issuing the grant.
            grantee_id: Agent receiving access.
            target_agent_id: Agent whose memories are shared.
            level: Permission level to grant.
            memory_type_filter: Optional restriction to specific memory types.
            expires_at: Optional ISO-8601 expiry timestamp.

        Returns:
            The created AccessGrant.
        """
        grant = AccessGrant(
            grantor_id=grantor_id,
            grantee_id=grantee_id,
            target_agent_id=target_agent_id,
            level=level,
            memory_type_filter=memory_type_filter,
            expires_at=expires_at,
        )
        self._grants[grant.id] = grant
        self._grants_index.setdefault((grantee_id, target_agent_id), []).append(grant)

        logger.info(
            "Grant %s: %s -> %s for agent %s (level=%s)",
            grant.id, grantor_id, grantee_id, target_agent_id, level.name,
        )
        return grant

    def revoke(self, grant_id: str) -> bool:
        """Revoke a specific grant.

        Returns:
            True if the grant existed and was removed.
        """
        grant = self._grants.pop(grant_id, None)
        if grant is None:
            return False

        key = (grant.grantee_id, grant.target_agent_id)
        grants = self._grants_index.get(key, [])
        self._grants_index[key] = [g for g in grants if g.id != grant_id]
        return True

    def revoke_all(self, grantor_id: str, grantee_id: str) -> int:
        """Revoke all grants from a specific grantor to a grantee.

        Returns:
            Number of grants revoked.
        """
        revoked = 0
        for gid in list(self._grants.keys()):
            grant = self._grants.get(gid)
            if grant and grant.grantor_id == grantor_id and grant.grantee_id == grantee_id:
                self.revoke(gid)
                revoked += 1
        return revoked

    # ── Permission checking ───────────────────────────────────────

    def check_permission(
        self,
        accessor_id: str,
        target_agent_id: str,
        action: str = ACTION_READ,
        memory_type: str | None = None,
    ) -> PermissionLevel:
        """Check the effective permission level for an access request.

        Resolution order:
        1. Self-access: L4 (admin) for own memories.
        2. Explicit grants: highest matching grant.
        3. Default: L2 (summary) for cross-agent access.

        Args:
            accessor_id: Agent requesting access.
            target_agent_id: Agent whose memory is being accessed.
            action: Action being performed (read/write/delete).
            memory_type: Optional memory type filter.

        Returns:
            The effective permission level.
        """
        if accessor_id == target_agent_id:
            return PermissionLevel.ADMIN

        key = (accessor_id, target_agent_id)
        grants = self._grants_index.get(key, [])

        max_level = PermissionLevel.NONE
        for grant in grants:
            if grant.is_expired:
                continue
            if grant.memory_type_filter and memory_type and grant.memory_type_filter != memory_type:
                continue
            if grant.level > max_level:
                max_level = grant.level

        if max_level > PermissionLevel.NONE:
            return max_level

        # Default cross-agent: summary only
        return PermissionLevel.SUMMARY

    def filter_content(
        self,
        accessor_id: str,
        target_agent_id: str,
        content: str,
        memory_type: str | None = None,
    ) -> str:
        """Filter content based on the accessor's permission level.

        Args:
            accessor_id: Agent requesting access.
            target_agent_id: Agent owning the memory.
            content: Full memory content.
            memory_type: Optional memory type for grant matching.

        Returns:
            Filtered content based on permission level.
        """
        level = self.check_permission(accessor_id, target_agent_id, memory_type=memory_type)

        if level >= PermissionLevel.FULL:
            return content
        if level == PermissionLevel.SUMMARY:
            return _generate_summary(content)
        if level == PermissionLevel.METADATA:
            return f"[{len(content)} chars, access restricted]"
        return "[access denied]"

    # ── Access logging ────────────────────────────────────────────

    def log_access(
        self,
        accessor_id: str,
        target_agent_id: str,
        memory_id: str,
        action: str = ACTION_READ,
        level_granted: PermissionLevel | None = None,
    ) -> None:
        """Log a memory access event."""
        if level_granted is None:
            level_granted = self.check_permission(accessor_id, target_agent_id, action)

        entry = AccessLogEntry(
            accessor_id=accessor_id,
            target_agent_id=target_agent_id,
            memory_id=memory_id,
            action=action,
            level_granted=level_granted,
        )
        self._access_log.append(entry)

        logger.debug(
            "Access log: %s -> %s/%s (action=%s, level=%s)",
            accessor_id, target_agent_id, memory_id, action, level_granted.name,
        )

    def get_access_log(
        self,
        accessor_id: str = "",
        target_agent_id: str = "",
        memory_id: str = "",
        limit: int = 100,
    ) -> list[AccessLogEntry]:
        """Query the access log with optional filters."""
        entries = self._access_log
        if accessor_id:
            entries = [e for e in entries if e.accessor_id == accessor_id]
        if target_agent_id:
            entries = [e for e in entries if e.target_agent_id == target_agent_id]
        if memory_id:
            entries = [e for e in entries if e.memory_id == memory_id]
        return list(reversed(entries[-limit:]))

    # ── Query ─────────────────────────────────────────────────────

    def list_grants(
        self,
        grantor_id: str = "",
        grantee_id: str = "",
    ) -> list[AccessGrant]:
        """List grants, optionally filtered by grantor or grantee."""
        grants = list(self._grants.values())
        if grantor_id:
            grants = [g for g in grants if g.grantor_id == grantor_id]
        if grantee_id:
            grants = [g for g in grants if g.grantee_id == grantee_id]
        return grants
