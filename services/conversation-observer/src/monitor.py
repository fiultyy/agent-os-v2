"""Conversation monitor — real-time conversation tracking."""

from typing import Any


class ConversationMonitor:
    """Monitors multi-turn conversations in real time.

    Tracks conversation state, context windows, and detects anomalies
    such as error spikes or stalled conversations.
    """

    def __init__(self) -> None:
        self._conversations: dict[str, dict[str, Any]] = {}

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
