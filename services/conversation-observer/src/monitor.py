"""Conversation monitor — real-time conversation tracking."""

import uuid
from datetime import datetime, timezone
from typing import Any


class ConversationMonitor:
    """Monitors multi-turn conversations in real time.

    Tracks conversation state, context windows, and detects anomalies
    such as error spikes or stalled conversations.
    """

    def __init__(self) -> None:
        self._conversations: dict[str, dict[str, Any]] = {}

    def create_conversation(
        self, conversation_id: str, agent_id: str = "", metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create and store a new conversation. Returns the conversation dict."""
        conv = {
            "id": conversation_id,
            "agent_id": agent_id,
            "turns": [],
            "metadata": metadata or {},
            "status": "active",
            "context_summary": "",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        self._conversations[conversation_id] = conv
        return conv

    def add_turn(
        self, conversation_id: str, role: str, content: str, metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Add a turn to a conversation. Returns the turn dict or None."""
        conv = self._conversations.get(conversation_id)
        if conv is None:
            return None
        turn = {
            "id": str(uuid.uuid4()),
            "role": role,
            "content": content,
            "metadata": metadata or {},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        conv["turns"].append(turn)
        conv["updated_at"] = datetime.now(timezone.utc).isoformat()
        return turn

    def delete_conversation(self, conversation_id: str) -> bool:
        """Delete a conversation. Returns True if found."""
        return self._conversations.pop(conversation_id, None) is not None

    def get_raw_conversation(self, conversation_id: str) -> dict[str, Any] | None:
        """Get the raw conversation dict for mutation (e.g. status updates)."""
        return self._conversations.get(conversation_id)

    async def on_message(self, conversation_id: str, message: dict[str, Any]) -> None:
        """Handle a new message in a conversation.

        Updates context summary and tracks last activity timestamp.
        """
        conv = self._conversations.get(conversation_id)
        if not conv:
            return

        role = message.get("role", "unknown")
        content = message.get("content", "")
        context = conv.get("context_summary", "")
        if context:
            context += f"\n{role}: {content[:200]}"
        else:
            context = f"{role}: {content[:200]}"
        # Keep context summary bounded to ~2000 chars
        conv["context_summary"] = context[-2000:]

    async def get_context(self, conversation_id: str) -> dict[str, Any]:
        """Get the current context of a conversation.

        Returns context summary, turn count, active status, and last activity.
        """
        conv = self._conversations.get(conversation_id)
        if not conv:
            return {"error": "Conversation not found"}

        turns = conv.get("turns", [])
        return {
            "conversation_id": conversation_id,
            "context_summary": conv.get("context_summary", ""),
            "turn_count": len(turns),
            "status": conv.get("status", "unknown"),
            "last_activity": conv.get("updated_at", ""),
            "roles_distribution": {
                role: sum(1 for t in turns if t.get("role") == role)
                for role in {t.get("role", "unknown") for t in turns}
            },
        }

    async def get_turns(self, conversation_id: str) -> list[dict[str, Any]]:
        """Get all turns in a conversation."""
        conv = self._conversations.get(conversation_id)
        if not conv:
            return []
        return conv.get("turns", [])

    # ── Public accessor methods ──────────────────────────────────────

    def get_conversation(self, conversation_id: str) -> dict[str, Any] | None:
        """Get a conversation by ID (returns a copy without internal keys)."""
        conv = self._conversations.get(conversation_id)
        if conv is None:
            return None
        return {
            "id": conv.get("id", conversation_id),
            "agent_id": conv.get("agent_id", ""),
            "status": conv.get("status", "unknown"),
            "turn_count": len(conv.get("turns", [])),
            "context_summary": conv.get("context_summary", ""),
            "created_at": conv.get("created_at", ""),
            "updated_at": conv.get("updated_at", ""),
        }

    def list_conversations(self, agent_id: str = "") -> list[dict[str, Any]]:
        """List all conversations, optionally filtered by agent_id.

        Returns a list of conversation summaries (no internal keys).
        """
        results = []
        for conv in self._conversations.values():
            if agent_id and conv.get("agent_id") != agent_id:
                continue
            results.append({
                "id": conv.get("id", ""),
                "agent_id": conv.get("agent_id", ""),
                "status": conv.get("status", "unknown"),
                "turn_count": len(conv.get("turns", [])),
                "context_summary": conv.get("context_summary", ""),
                "created_at": conv.get("created_at", ""),
                "updated_at": conv.get("updated_at", ""),
            })
        return results

    def get_stats(self, time_range: str = "7d") -> dict[str, Any]:
        """Get aggregate conversation statistics."""
        total = len(self._conversations)
        active = sum(1 for c in self._conversations.values() if c.get("status") == "active")
        total_turns = sum(len(c.get("turns", [])) for c in self._conversations.values())
        return {
            "total_conversations": total,
            "active_conversations": active,
            "total_turns": total_turns,
            "time_range": time_range,
        }
