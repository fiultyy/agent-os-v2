"""L2: communication bridge (back-end side).

Two wiring gaps closed by L2:
1. ``CommunicationBus.register_agent`` was never called at session start, so
   ``broadcast`` delivered to an empty session (0 recipients). ``/execute`` now
   registers the executing agent into the session.
2. ``CommunicationBus.register_delivery_callback`` had zero call-sites; engine.py
   now registers a bridge that turns a delivered AgentMessage into an
   ``agent_message`` SSE event (front-end dispatch is L4).
"""

import pytest

from src.communication.bus import CommunicationBus
from src.communication.message import AgentMessage, MessageType
from src.services import _state


def _msg(sender: str, content: str, *, recipient: str | None = None,
         session: str = "sess-1") -> AgentMessage:
    return AgentMessage(
        sender_id=sender,
        recipient_id=recipient,
        session_id=session,
        content=content,
        message_type=MessageType.NOTIFICATION,
    )


@pytest.mark.asyncio
async def test_register_agent_makes_broadcast_deliver() -> None:
    bus = CommunicationBus()
    bus.register_agent("alpha", "sess-1")
    bus.register_agent("beta", "sess-1")

    delivered = await bus.broadcast(_msg("alpha", "hello"), session_id="sess-1")

    # beta is the only other session member (alpha is sender → skipped).
    assert len(delivered) == 1
    assert delivered[0].endswith("-beta")
    assert bus.pending_count("beta") == 1


@pytest.mark.asyncio
async def test_broadcast_zero_without_registration() -> None:
    """The original bug: no register_agent → broadcast delivers to nobody."""
    bus = CommunicationBus()
    delivered = await bus.broadcast(_msg("alpha", "x"), session_id="sess-1")
    assert delivered == []


@pytest.mark.asyncio
async def test_delivery_callback_fires_on_direct_send() -> None:
    """register_delivery_callback (was zero-call) now bridges direct deliveries."""
    bus = CommunicationBus()
    bus.register_agent("alpha", "sess-1")
    bus.register_agent("beta", "sess-1")

    received: list[tuple[str, str]] = []

    async def _capture(message: AgentMessage, recipient_id: str) -> None:
        received.append((recipient_id, message.content))

    bus.register_delivery_callback(_capture)

    await bus.send(_msg("alpha", "direct-deliver", recipient="beta"))

    assert received == [("beta", "direct-deliver")]


@pytest.mark.asyncio
async def test_emit_agent_message_surfaces_on_sse_stream() -> None:
    """_state.emit_agent_message pushes an agent_message SSE event subscribers see."""
    q = _state.subscribe_memory_events()
    try:
        _state.emit_agent_message(_msg("alpha", "hi", recipient="beta"), "beta")
        sse = q.get_nowait()
        assert sse.startswith("event: agent_message\n")
        assert "alpha" in sse
        assert "beta" in sse
    finally:
        _state.unsubscribe_memory_events(q)


@pytest.mark.asyncio
async def test_engine_bridge_callback_emits_agent_message() -> None:
    """The exact callback engine.py registers: send → agent_message on the SSE stream."""
    # Re-create the bridge wiring (engine.py registers this on _state.communication_bus).
    bus = CommunicationBus()
    bus.register_agent("alpha", "sess-1")
    bus.register_agent("beta", "sess-1")

    q = _state.subscribe_memory_events()
    try:
        async def _bridge(message, recipient_id):
            _state.emit_agent_message(message, recipient_id)

        bus.register_delivery_callback(_bridge)
        await bus.send(_msg("alpha", "bridged", recipient="beta"))

        sse = q.get_nowait()
        assert sse.startswith("event: agent_message\n")
        assert "bridged" in sse
    finally:
        _state.unsubscribe_memory_events(q)
