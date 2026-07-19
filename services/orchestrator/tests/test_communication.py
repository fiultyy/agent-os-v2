"""L2: communication bridge (back-end side).

Wiring gap closed by L2: ``CommunicationBus.register_agent`` was never called
at session start, so ``broadcast`` delivered to an empty session (0
recipients). ``/execute`` now registers the executing agent into the session.

Part2 removed the legacy ``agent_message`` SSE bridge (engine.py callback +
_state.emit_agent_message + the memory SSE subscriber stream) — agent-message
delivery now flows over the observe channel like the rest of memory lifecycle.
The two SSE-bridge tests were deleted with it; the bus behaviour tests below
(register_agent / register_delivery_callback / broadcast fan-out) remain.
"""

import pytest

from src.communication.bus import CommunicationBus
from src.communication.message import AgentMessage, MessageType


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
async def test_delivery_callback_fires_on_broadcast() -> None:
    """broadcast() fires delivery callbacks per recipient — was send-only.

    Regression guard for the /execute + /orchestrate broadcast paths: without
    this, agent_message SSE never reaches the front-end CommunicationPanel in
    orchestration scenarios (each recipient must get its own callback so the
    bridge emits one agent_message SSE per delivered copy).
    """
    bus = CommunicationBus()
    bus.register_agent("alpha", "sess-1")
    bus.register_agent("beta", "sess-1")
    bus.register_agent("gamma", "sess-1")

    received: list[tuple[str, str]] = []

    async def _capture(message: AgentMessage, recipient_id: str) -> None:
        received.append((recipient_id, message.content))

    bus.register_delivery_callback(_capture)

    delivered = await bus.broadcast(_msg("alpha", "broadcast-hi"), session_id="sess-1")

    # beta + gamma receive (alpha is sender → skipped); each fires the callback.
    assert sorted(received) == [("beta", "broadcast-hi"), ("gamma", "broadcast-hi")]
    assert len(delivered) == len(received) == 2
