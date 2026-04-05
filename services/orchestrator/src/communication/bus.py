"""CommunicationBus — message passing between agents.

Supports:
- Direct agent-to-agent messaging
- Broadcast to all agents in a session or workspace
- Topic-based pub/sub
- Request/response pattern with correlation IDs and timeouts
- Scope-based message filtering via ScopeManager
- Message delivery history and tracking
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Callable, Awaitable

from src.communication.message import (
    AgentMessage,
    DeliveryStatus,
    MessagePriority,
    MessageType,
)
from src.communication.scope import ScopeManager, ScopeLevel

logger = logging.getLogger(__name__)


class CommunicationBus:
    """Central message bus for inter-agent communication.

    Maintains per-agent message queues for direct messaging,
    session-scoped subscriptions for broadcasts, topic-based
    pub/sub channels, and delivery history tracking.
    """

    def __init__(
        self,
        scope_manager: ScopeManager | None = None,
        max_history_per_agent: int = 200,
    ) -> None:
        # Per-agent incoming message queues
        self._queues: dict[str, asyncio.Queue[AgentMessage]] = {}
        # session_id -> set of agent_ids in that session
        self._session_members: dict[str, set[str]] = {}
        # workspace_id -> set of agent_ids in that workspace
        self._workspace_members: dict[str, set[str]] = {}
        # topic -> list of subscriber callbacks
        self._topic_subscribers: dict[
            str, list[Callable[[AgentMessage], Awaitable[None]]]
        ] = {}
        # Pending request/response correlation: correlation_id -> Future
        self._pending_responses: dict[str, asyncio.Future[AgentMessage]] = {}
        # Scope manager for trust-domain routing
        self._scope_manager = scope_manager or ScopeManager()
        # Delivery history: agent_id -> list of recent messages
        self._history: dict[str, list[AgentMessage]] = defaultdict(list)
        self._max_history = max_history_per_agent
        # Message delivery callbacks
        self._delivery_callbacks: list[Callable[[AgentMessage, str], Awaitable[None]]] = []

    @property
    def scope_manager(self) -> ScopeManager:
        """Access the scope manager for membership queries."""
        return self._scope_manager

    # ── Queue management ─────────────────────────────────────────

    def _ensure_queue(self, agent_id: str) -> asyncio.Queue[AgentMessage]:
        if agent_id not in self._queues:
            self._queues[agent_id] = asyncio.Queue()
        return self._queues[agent_id]

    def _record_history(self, agent_id: str, message: AgentMessage) -> None:
        """Record a message in the agent's delivery history."""
        history = self._history[agent_id]
        history.append(message)
        if len(history) > self._max_history:
            # Trim oldest messages
            self._history[agent_id] = history[-self._max_history:]

    # ── Direct messaging ─────────────────────────────────────────

    async def send(self, message: AgentMessage) -> str:
        """Send a message to a specific agent, return message ID.

        If ``message.id`` is empty a new UUID is assigned.
        The message is enqueued in the recipient's queue and
        recorded in delivery history.

        Args:
            message: The message to deliver.

        Returns:
            The message ID.

        Raises:
            ValueError: If no recipient_id is specified.
        """
        if not message.recipient_id:
            raise ValueError("Direct send requires a recipient_id")

        if not message.id:
            message.id = str(uuid.uuid4())
        if not message.timestamp:
            message.timestamp = datetime.now(timezone.utc).isoformat()

        message.delivery_status = DeliveryStatus.DELIVERED
        queue = self._ensure_queue(message.recipient_id)
        await queue.put(message)

        # Record in both sender and recipient history
        self._record_history(message.sender_id, message)
        self._record_history(message.recipient_id, message)

        # Fire delivery callbacks
        for cb in self._delivery_callbacks:
            try:
                await cb(message, message.recipient_id)
            except Exception as exc:
                logger.warning("Delivery callback error: %s", exc)

        return message.id

    async def receive(
        self, agent_id: str, timeout: float = 5.0,
    ) -> AgentMessage | None:
        """Receive the next message for *agent_id*, or ``None`` on timeout.

        Expired messages are silently discarded.

        Args:
            agent_id: Agent to receive for.
            timeout: Max seconds to wait.

        Returns:
            The next valid message, or None.
        """
        queue = self._ensure_queue(agent_id)
        deadline = asyncio.get_event_loop().time() + timeout

        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                return None

            try:
                msg = await asyncio.wait_for(queue.get(), timeout=max(0.01, remaining))
            except asyncio.TimeoutError:
                return None

            # Discard expired messages
            if msg.is_expired:
                msg.delivery_status = DeliveryStatus.EXPIRED
                logger.debug("Discarding expired message %s", msg.id)
                continue

            msg.delivery_status = DeliveryStatus.ACKNOWLEDGED
            return msg

    # ── Broadcast ─────────────────────────────────────────────────

    def register_agent(self, agent_id: str, session_id: str, workspace_id: str = "") -> None:
        """Register an agent as part of a session for broadcast delivery.

        Args:
            agent_id: Agent to register.
            session_id: Session to join.
            workspace_id: Optional workspace to join.
        """
        self._session_members.setdefault(session_id, set()).add(agent_id)
        self._ensure_queue(agent_id)

        if workspace_id:
            self._workspace_members.setdefault(workspace_id, set()).add(agent_id)

    def unregister_agent(self, agent_id: str, session_id: str, workspace_id: str = "") -> None:
        """Remove an agent from a session's broadcast list."""
        members = self._session_members.get(session_id)
        if members and agent_id in members:
            members.discard(agent_id)

        if workspace_id:
            ws_members = self._workspace_members.get(workspace_id)
            if ws_members and agent_id in ws_members:
                ws_members.discard(agent_id)

    async def broadcast(
        self,
        message: AgentMessage,
        session_id: str = "",
        workspace_id: str = "",
    ) -> list[str]:
        """Broadcast a message to all agents in a session and/or workspace.

        Args:
            message: The message to broadcast.
            session_id: If set, broadcast to all agents in this session.
            workspace_id: If set, broadcast to all agents in this workspace.

        Returns:
            List of delivered message IDs.
        """
        if not message.id:
            message.id = str(uuid.uuid4())
        message.message_type = MessageType.BROADCAST
        message.timestamp = datetime.now(timezone.utc).isoformat()

        recipients: set[str] = set()
        if session_id:
            recipients.update(self._session_members.get(session_id, set()))
        if workspace_id:
            recipients.update(self._workspace_members.get(workspace_id, set()))

        delivered_ids: list[str] = []
        for agent_id in recipients:
            if agent_id == message.sender_id:
                continue  # don't echo to sender

            copy = AgentMessage(
                id=f"{message.id}-{agent_id}",
                sender_id=message.sender_id,
                recipient_id=agent_id,
                session_id=session_id,
                workspace_id=workspace_id,
                message_type=message.message_type,
                content=message.content,
                payload=dict(message.payload),
                correlation_id=message.correlation_id,
                timestamp=message.timestamp,
                priority=message.priority,
                ttl_seconds=message.ttl_seconds,
            )
            copy.delivery_status = DeliveryStatus.DELIVERED
            await self._queues[agent_id].put(copy)
            self._record_history(agent_id, copy)
            delivered_ids.append(copy.id)

        return delivered_ids

    # ── Topic-based pub/sub ───────────────────────────────────────

    def subscribe(
        self,
        topic: str,
        handler: Callable[[AgentMessage], Awaitable[None]],
    ) -> None:
        """Subscribe *handler* to messages published on *topic*."""
        self._topic_subscribers.setdefault(topic, []).append(handler)

    def unsubscribe(
        self, topic: str, handler: Callable[[AgentMessage], Awaitable[None]],
    ) -> None:
        """Remove a handler from a topic."""
        subs = self._topic_subscribers.get(topic, [])
        self._topic_subscribers[topic] = [h for h in subs if h is not handler]

    async def publish(self, topic: str, message: AgentMessage) -> int:
        """Publish *message* to all subscribers of *topic*.

        Args:
            topic: Topic channel to publish on.
            message: Message to publish.

        Returns:
            Number of subscribers that received the message.
        """
        if not message.id:
            message.id = str(uuid.uuid4())
        message.timestamp = datetime.now(timezone.utc).isoformat()
        message.payload["topic"] = topic

        handlers = self._topic_subscribers.get(topic, [])
        for handler in handlers:
            try:
                await handler(message)
            except Exception as exc:
                logger.warning("Topic handler error on %s: %s", topic, exc)

        return len(handlers)

    # ── Request / Response ────────────────────────────────────────

    async def request(
        self,
        message: AgentMessage,
        timeout: float = 10.0,
    ) -> AgentMessage | None:
        """Send a request and wait for a correlated response.

        Sets ``correlation_id`` on the outgoing message and blocks
        until a matching response arrives or *timeout* elapses.

        Args:
            message: The request message.
            timeout: Max seconds to wait for a response.

        Returns:
            The response message, or None on timeout.
        """
        correlation_id = str(uuid.uuid4())
        message.correlation_id = correlation_id
        message.message_type = MessageType.REQUEST

        await self.send(message)

        loop = asyncio.get_running_loop()
        future: asyncio.Future[AgentMessage] = loop.create_future()
        self._pending_responses[correlation_id] = future

        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            logger.debug("Request %s timed out after %.1fs", correlation_id, timeout)
            return None
        finally:
            self._pending_responses.pop(correlation_id, None)

    async def respond(self, response: AgentMessage) -> str:
        """Deliver a response message, resolving the pending request future.

        If the response correlates to a pending request, the future is
        resolved so the requester's ``request()`` call returns.
        """
        cid = response.correlation_id
        if cid and cid in self._pending_responses:
            future = self._pending_responses.pop(cid)
            if not future.done():
                future.set_result(response)

        response.message_type = MessageType.RESPONSE
        return await self.send(response)

    # ── Scope-aware send ──────────────────────────────────────────

    async def send_to_scope(
        self,
        message: AgentMessage,
        scope_id: str,
    ) -> list[str]:
        """Send a message to all agents in a scope.

        Uses the ScopeManager to determine which agents are members
        of the specified scope.

        Args:
            message: The message to deliver.
            scope_id: Target scope identifier.

        Returns:
            List of delivered message IDs.
        """
        members = self._scope_manager.agents_in_scope(scope_id)
        delivered: list[str] = []

        for agent_id in members:
            if agent_id == message.sender_id:
                continue

            copy = AgentMessage(
                sender_id=message.sender_id,
                recipient_id=agent_id,
                session_id=message.session_id,
                workspace_id=message.workspace_id,
                message_type=message.message_type,
                content=message.content,
                payload=dict(message.payload),
                correlation_id=message.correlation_id,
                priority=message.priority,
            )
            msg_id = await self.send(copy)
            delivered.append(msg_id)

        return delivered

    # ── Query ─────────────────────────────────────────────────────

    def pending_count(self, agent_id: str) -> int:
        """Return the number of queued messages for *agent_id*."""
        q = self._queues.get(agent_id)
        return q.qsize() if q else 0

    def get_history(
        self,
        agent_id: str,
        limit: int = 50,
        message_type: MessageType | None = None,
    ) -> list[AgentMessage]:
        """Get message delivery history for an agent.

        Args:
            agent_id: Agent to query.
            limit: Maximum messages to return.
            message_type: Optional filter by message type.

        Returns:
            List of historical messages, newest first.
        """
        history = self._history.get(agent_id, [])
        if message_type:
            history = [m for m in history if m.message_type == message_type]
        return list(reversed(history[-limit:]))

    def register_delivery_callback(
        self, callback: Callable[[AgentMessage, str], Awaitable[None]],
    ) -> None:
        """Register a callback invoked when a message is delivered.

        The callback receives (message, recipient_agent_id).
        """
        self._delivery_callbacks.append(callback)

    async def close(self) -> None:
        """Clear all queues and subscriptions on shutdown."""
        self._queues.clear()
        self._session_members.clear()
        self._workspace_members.clear()
        self._topic_subscribers.clear()
        self._pending_responses.clear()
        self._delivery_callbacks.clear()
        self._history.clear()
        logger.info("CommunicationBus closed")
