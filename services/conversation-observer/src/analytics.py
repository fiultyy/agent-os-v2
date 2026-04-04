"""Conversation analytics — usage stats and insights."""

from datetime import datetime, timezone
from typing import Any


class ConversationAnalytics:
    """Analyzes conversation patterns and generates insights."""

    def __init__(self) -> None:
        self._event_log: list[dict[str, Any]] = []

    def record_event(self, event_type: str, data: dict[str, Any]) -> None:
        """Record an analytics event."""
        self._event_log.append({
            "type": event_type,
            "data": data,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    async def get_stats(self, time_range: str = "7d") -> dict[str, Any]:
        """Get conversation statistics for a time range.

        Returns aggregate metrics from the event log.
        """
        total_events = len(self._event_log)
        event_types: dict[str, int] = {}
        for event in self._event_log:
            key = event.get("type", "unknown")
            event_types[key] = event_types.get(key, 0) + 1

        return {
            "total_events": total_events,
            "event_types": event_types,
            "time_range": time_range,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
