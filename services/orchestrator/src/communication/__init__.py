"""Agent communication module."""

from src.communication.bus import CommunicationBus
from src.communication.message import AgentMessage
from src.communication.scope import ScopeManager

__all__ = ["CommunicationBus", "AgentMessage", "ScopeManager"]
