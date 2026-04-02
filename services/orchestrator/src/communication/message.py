"""Message type definitions for inter-agent communication."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class MessageType(str, Enum):
    """Types of agent messages."""

    TASK = "task"
    RESULT = "result"
    ERROR = "error"
    BROADCAST = "broadcast"
    HEARTBEAT = "heartbeat"


@dataclass
class AgentMessage:
    """A message exchanged between agents."""

    id: str = ""
    sender_id: str = ""
    recipient_id: str | None = None  # None = broadcast
    session_id: str = ""
    message_type: MessageType = MessageType.TASK
    content: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    correlation_id: str | None = None  # For request/response pairing
    timestamp: str = ""
