"""Message type definitions for inter-agent communication.

Provides structured message types for all communication patterns:
- Direct agent-to-agent messaging
- Request/response with correlation IDs
- Broadcast within sessions/workspaces
- Topic-based pub/sub
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class MessageType(str, Enum):
    """Types of agent messages."""

    TASK = "task"
    RESULT = "result"
    ERROR = "error"
    BROADCAST = "broadcast"
    HEARTBEAT = "heartbeat"
    NOTIFICATION = "notification"
    REQUEST = "request"
    RESPONSE = "response"
    SYNC = "sync"           # Shared state synchronization


class MessagePriority(int, Enum):
    """Message delivery priority — higher value = more urgent."""

    LOW = 0
    NORMAL = 1
    HIGH = 2
    CRITICAL = 3


class DeliveryStatus(str, Enum):
    """Delivery status for a message."""

    PENDING = "pending"
    DELIVERED = "delivered"
    ACKNOWLEDGED = "acknowledged"
    FAILED = "failed"
    EXPIRED = "expired"


@dataclass
class AgentMessage:
    """A message exchanged between agents.

    Attributes:
        id: Unique message identifier (UUID).
        sender_id: Agent ID of the sender.
        recipient_id: Agent ID of the recipient, or None for broadcast.
        session_id: Session this message belongs to.
        workspace_id: Workspace this message belongs to.
        message_type: Classification of the message.
        priority: Delivery priority.
        content: Human-readable message text.
        payload: Structured data attached to the message.
        correlation_id: For request/response pairing.
        reply_to: ID of the message this is a reply to.
        timestamp: ISO-8601 creation timestamp.
        ttl_seconds: Time-to-live; message expires after this many seconds.
        delivery_status: Current delivery status.
        metadata: Extra key-value metadata.
    """

    id: str = ""
    sender_id: str = ""
    recipient_id: str | None = None  # None = broadcast
    session_id: str = ""
    workspace_id: str = ""
    message_type: MessageType = MessageType.TASK
    priority: MessagePriority = MessagePriority.NORMAL
    content: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    correlation_id: str | None = None  # For request/response pairing
    reply_to: str | None = None
    timestamp: str = ""
    ttl_seconds: float = 0.0  # 0 = no expiry
    delivery_status: DeliveryStatus = DeliveryStatus.PENDING
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.id:
            self.id = str(uuid.uuid4())
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    @property
    def is_expired(self) -> bool:
        """Check if the message has exceeded its TTL."""
        if self.ttl_seconds <= 0:
            return False
        try:
            created = datetime.fromisoformat(self.timestamp)
            age = (datetime.now(timezone.utc) - created).total_seconds()
            return age > self.ttl_seconds
        except (ValueError, TypeError):
            return False

    def create_reply(
        self,
        content: str = "",
        payload: dict[str, Any] | None = None,
        message_type: MessageType = MessageType.RESPONSE,
    ) -> "AgentMessage":
        """Create a reply message with correlation ID set.

        Args:
            content: Reply content text.
            payload: Optional structured data.
            message_type: Message type for the reply.

        Returns:
            A new AgentMessage addressed back to the sender.
        """
        return AgentMessage(
            sender_id=self.recipient_id or "",
            recipient_id=self.sender_id,
            session_id=self.session_id,
            workspace_id=self.workspace_id,
            message_type=message_type,
            content=content,
            payload=payload or {},
            correlation_id=self.id,
            reply_to=self.id,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dictionary."""
        return {
            "id": self.id,
            "sender_id": self.sender_id,
            "recipient_id": self.recipient_id,
            "session_id": self.session_id,
            "workspace_id": self.workspace_id,
            "message_type": self.message_type.value,
            "priority": self.priority.value,
            "content": self.content,
            "payload": self.payload,
            "correlation_id": self.correlation_id,
            "reply_to": self.reply_to,
            "timestamp": self.timestamp,
            "ttl_seconds": self.ttl_seconds,
            "delivery_status": self.delivery_status.value,
            "metadata": self.metadata,
        }
