"""CommunicationBus — message passing between agents.

Supports:
- Direct agent-to-agent messaging
- Broadcast to all agents in a session
- Topic-based pub/sub
- Request/response pattern with correlation IDs
"""

from typing import Any

from src.communication.message import AgentMessage


class CommunicationBus:
    """Central message bus for inter-agent communication."""

    async def send(self, message: AgentMessage) -> str:
        """Send a message, return message ID."""
        # TODO: implement message routing
        return message.id

    async def receive(
        self, agent_id: str, timeout: float = 5.0
    ) -> AgentMessage | None:
        """Receive next message for an agent."""
        # TODO: implement message queue
        return None

    async def broadcast(self, message: AgentMessage, session_id: str) -> None:
        """Broadcast a message to all agents in a session."""
        # TODO: implement broadcast
        pass
