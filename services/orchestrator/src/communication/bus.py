"""CommunicationBus — message passing between agents.

Supports:
- Direct agent-to-agent messaging
- Broadcast to all agents in a session
- Topic-based pub/sub
- Request/response pattern with correlation IDs
"""

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Awaitable

from src.communication.message import AgentMessage, MessageType


class CommunicationBus:
    """Central message bus for inter-agent communication.

    Maintains per-agent message queues for direct messaging,
    session-scoped subscriptions for broadcasts, and topic-based
    pub/sub channels.
    """

    def __init__(self) -> None:
        # Per-agent incoming message queues
        self._queues: dict[str, asyncio.Queue[AgentMessage]] = {}
        # session_id -> set of agent_ids in that session
        self._session_members: dict[str, set[str]] = {}
        # topic -> list of subscriber callbacks
        self._topic_subscribers: dict[str, list[Callable[[AgentMessage], Awaitable[None]]]] = {}
        # Pending request/response correlation: correlation_id -> Future
        self._pending_responses: dict[str, asyncio.Future[AgentMessage]] = {}

    # ── Direct messaging ─────────────────────────────────────────

    def _ensure_queue(self, agent_id: str) -> asyncio.Queue[AgentMessage]:
        if agent_id not in self._queues:
            self._queues[agent_id] = asyncio.Queue()
        return self._queues[agent_id]

    async def send(self, message: AgentMessage) -> str:
        """Send a message to a specific agent, return message ID.

        If ``message.id`` is empty a new UUID is assigned.
        The message is enqueued in the recipient's queue.
        """
        if not message.id:
            message.id = str(uuid.uuid4())
        if not message.timestamp:
            message.timestamp = datetime.now(timezone.utc).isoformat()

        if message.recipient_id:
            queue = self._ensure_queue(message.recipient_id)
            await queue.put(message)
        return message.id

    async def receive(
        self, agent_id: str, timeout: float = 5.0
    ) -> AgentMessage | None:
        """Receive the next message for *agent_id*, or ``None`` on timeout."""
        queue = self._ensure_queue(agent_id)
        try:
            return await asyncio.wait_for(queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None

    # ── Broadcast ─────────────────────────────────────────────────

    def register_agent(self, agent_id: str, session_id: str) -> None:
        """Register an agent as part of a session for broadcast delivery."""
        self._session_members.setdefault(session_id, set()).add(agent_id)
        self._ensure_queue(agent_id)

    def unregister_agent(self, agent_id: str, session_id: str) -> None:
        """Remove an agent from a session's broadcast list."""
        members = self._session_members.get(session_id)
        if members and agent_id in members:
            members.discard(agent_id)

    async def broadcast(self, message: AgentMessage, session_id: str) -> None:
        """Broadcast a message to all agents registered in *session_id*."""
        if not message.id:
            message.id = str(uuid.uuid4())
        message.message_type = MessageType.BROADCAST
        message.timestamp = datetime.now(timezone.utc).isoformat()

        members = self._session_members.get(session_id, set())
        for agent_id in members:
            if agent_id == message.sender_id:
                continue  # don't echo to sender
            copy = AgentMessage(
                id=f"{message.id}-{agent_id}",
                sender_id=message.sender_id,
                recipient_id=agent_id,
                session_id=session_id,
                message_type=message.message_type,
                content=message.content,
                payload=dict(message.payload),
                correlation_id=message.correlation_id,
                timestamp=message.timestamp,
            )
            await self._queues[agent_id].put(copy)

    # ── Topic-based pub/sub ───────────────────────────────────────

    def subscribe(
        self,
        topic: str,
        handler: Callable[[AgentMessage], Awaitable[None]],
    ) -> None:
        """Subscribe *handler* to messages published on *topic*."""
        self._topic_subscribers.setdefault(topic, []).append(handler)

    def unsubscribe(self, topic: str, handler: Callable[[AgentMessage], Awaitable[None]]) -> None:
        """Remove a handler from a topic."""
        subs = self._topic_subscribers.get(topic, [])
        self._topic_subscribers[topic] = [h for h in subs if h is not handler]

    async def publish(self, topic: str, message: AgentMessage) -> None:
        """Publish *message* to all subscribers of *topic*."""
        if not message.id:
            message.id = str(uuid.uuid4())
        message.timestamp = datetime.now(timezone.utc).isoformat()
        message.payload["topic"] = topic

        for handler in self._topic_subscribers.get(topic, []):
            await handler(message)

    # ── Request / Response ────────────────────────────────────────

    async def request(
        self,
        message: AgentMessage,
        timeout: float = 10.0,
    ) -> AgentMessage | None:
        """Send a request and wait for a correlated response.

        Sets ``correlation_id`` on the outgoing message and blocks
        until a matching response arrives or *timeout* elapses.
        """
        correlation_id = str(uuid.uuid4())
        message.correlation_id = correlation_id
        await self.send(message)

        loop = asyncio.get_running_loop()
        future: asyncio.Future[AgentMessage] = loop.create_future()
        self._pending_responses[correlation_id] = future

        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            return None
        finally:
            self._pending_responses.pop(correlation_id, None)

    async def respond(self, response: AgentMessage) -> str:
        """Deliver a response message, resolving the pending request future."""
        cid = response.correlation_id
        if cid and cid in self._pending_responses:
            future = self._pending_responses.pop(cid)
            if not future.done():
                future.set_result(response)
        return await self.send(response)

    # ── Query ─────────────────────────────────────────────────────

    def pending_count(self, agent_id: str) -> int:
        """Return the number of queued messages for *agent_id*."""
        q = self._queues.get(agent_id)
        return q.qsize() if q else 0
