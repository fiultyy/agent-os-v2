"""Conversation monitor — real-time conversation tracking."""

from typing import Any


class ConversationMonitor:
    """Monitors multi-turn conversations in real time."""

    async def on_message(self, conversation_id: str, message: dict[str, Any]) -> None:
        """Handle a new message in a conversation."""
        pass

    async def get_context(self, conversation_id: str) -> dict[str, Any]:
        """Get the current context of a conversation."""
        return {}

    async def get_turns(self, conversation_id: str) -> list[dict[str, Any]]:
        """Get all turns in a conversation."""
        return []
