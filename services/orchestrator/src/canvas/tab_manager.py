"""P0-7: TabManager — maps browser tabs to session/branch.

A single canvas session may be viewed in multiple browser tabs,
each potentially on a different branch.  TabManager tracks these
associations so the WebSocket gateway can route events correctly.

All state is kept in memory (suitable for single-process deployments).
For multi-process, replace with Redis or shared KV store.
"""

from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

from src.canvas.branch import Branch

logger = logging.getLogger(__name__)


@dataclass
class Tab:
    """Represents a browser tab's association with a canvas branch."""
    tab_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str = ""
    branch_id: str = "main"
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    last_active_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict:
        return {
            "tab_id": self.tab_id,
            "session_id": self.session_id,
            "branch_id": self.branch_id,
            "created_at": self.created_at,
            "last_active_at": self.last_active_at,
        }


class TabManager:
    """In-memory session-tab-branch association manager.

    Provides O(1) lookups for:
    - tab → branch (which branch is this tab viewing?)
    - branch → tabs (which tabs are viewing this branch?)
    - session → tabs (all tabs for a session)
    """

    def __init__(self) -> None:
        # tab_id -> Tab
        self._tabs: Dict[str, Tab] = {}
        # branch_id -> set of tab_ids
        self._branch_tabs: Dict[str, set] = {}
        # session_id -> set of tab_ids
        self._session_tabs: Dict[str, set] = {}
        # Lock to protect state mutations in async context.
        # threading.Lock suffices because all mutating methods are synchronous
        # (no await points), so the event loop won't switch coroutines mid-call.
        self._lock = threading.Lock()

    # ── Create ──────────────────────────────────────────────────────

    def create_tab(self, session_id: str, branch_id: str = "main") -> Tab:
        """Create a new tab associated with a session and branch.

        Args:
            session_id: The canvas session.
            branch_id: The branch this tab is viewing (default "main").

        Returns:
            The created Tab object.
        """
        tab = Tab(session_id=session_id, branch_id=branch_id)
        with self._lock:
            self._tabs[tab.tab_id] = tab

            # Index by branch
            if branch_id not in self._branch_tabs:
                self._branch_tabs[branch_id] = set()
            self._branch_tabs[branch_id].add(tab.tab_id)

            # Index by session
            if session_id not in self._session_tabs:
                self._session_tabs[session_id] = set()
            self._session_tabs[session_id].add(tab.tab_id)

        logger.debug("Tab created: %s session=%s branch=%s",
                      tab.tab_id, session_id, branch_id)
        return tab

    # ── Read ────────────────────────────────────────────────────────

    def get_tab(self, tab_id: str) -> Optional[Tab]:
        """Get a tab by ID."""
        return self._tabs.get(tab_id)

    def get_branch_for_tab(self, tab_id: str) -> Optional[Branch]:
        """Get the branch associated with a tab.

        Returns None if the tab doesn't exist.
        Note: Returns a lightweight Branch proxy with branch_id set.
              For full branch data, query the BranchManager.
        """
        tab = self._tabs.get(tab_id)
        if tab is None:
            return None
        return Branch(branch_id=tab.branch_id, session_id=tab.session_id)

    def get_tabs_for_branch(self, branch_id: str) -> List[Tab]:
        """Get all tabs currently viewing a branch."""
        tab_ids = self._branch_tabs.get(branch_id, set())
        tabs = []
        for tid in tab_ids:
            tab = self._tabs.get(tid)
            if tab:
                tabs.append(tab)
        return tabs

    def get_tabs_for_session(self, session_id: str) -> List[Tab]:
        """Get all tabs for a session."""
        tab_ids = self._session_tabs.get(session_id, set())
        tabs = []
        for tid in tab_ids:
            tab = self._tabs.get(tid)
            if tab:
                tabs.append(tab)
        return tabs

    # ── Update ──────────────────────────────────────────────────────

    def switch_branch(self, tab_id: str, new_branch_id: str) -> Optional[Tab]:
        """Move a tab to a different branch.

        Returns the updated Tab, or None if tab doesn't exist.
        """
        with self._lock:
            tab = self._tabs.get(tab_id)
            if tab is None:
                return None

            old_branch_id = tab.branch_id

            # Remove from old branch index
            old_tabs = self._branch_tabs.get(old_branch_id, set())
            old_tabs.discard(tab_id)

            # Add to new branch index
            if new_branch_id not in self._branch_tabs:
                self._branch_tabs[new_branch_id] = set()
            self._branch_tabs[new_branch_id].add(tab_id)

            # Update tab
            tab.branch_id = new_branch_id
            tab.last_active_at = datetime.now(timezone.utc).isoformat()

        logger.debug("Tab %s switched branch: %s → %s",
                      tab_id, old_branch_id, new_branch_id)
        return tab

    def touch(self, tab_id: str) -> None:
        """Update last_active_at for a tab."""
        with self._lock:
            tab = self._tabs.get(tab_id)
            if tab:
                tab.last_active_at = datetime.now(timezone.utc).isoformat()

    # ── Delete ──────────────────────────────────────────────────────

    def close_tab(self, tab_id: str) -> bool:
        """Remove a tab. Returns True if found and removed."""
        with self._lock:
            tab = self._tabs.pop(tab_id, None)
            if tab is None:
                return False

            # Clean up branch index
            branch_tabs = self._branch_tabs.get(tab.branch_id, set())
            branch_tabs.discard(tab_id)
            if not branch_tabs:
                self._branch_tabs.pop(tab.branch_id, None)

            # Clean up session index
            session_tabs = self._session_tabs.get(tab.session_id, set())
            session_tabs.discard(tab_id)
            if not session_tabs:
                self._session_tabs.pop(tab.session_id, None)

        logger.debug("Tab closed: %s", tab_id)
        return True

    # ── Stats ───────────────────────────────────────────────────────

    @property
    def total_tabs(self) -> int:
        return len(self._tabs)

    def session_count(self) -> int:
        return len(self._session_tabs)
