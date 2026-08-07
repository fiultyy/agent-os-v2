"""A2A LocalTransport — in-process message/send (ADR-3, ADR-5).

Resolves a target agent and runs a fresh turn against it via the shared
``assemble_capabilities`` + ``build_native_agent`` path. Zero network: pure
in-process function call. MVP synchronous send → completed.

ADR-4 invariant: the consumed agent runs as a PEER with its full capability
stack, scoped to its OWN agent_id — the MemoryWriterCapability receives the
*target* agent_id, never the caller's. R1 (workflow subagent zero-writer) is a
different axis and is not touched here.

No httpx / socket / aiohttp — verified by an AST scan in the unit test.
"""
from __future__ import annotations

import logging
import os
import uuid
from typing import Any

from pydantic import BaseModel, Field

from agent.agent_spec import AgentSpec, normalize_agent_id

logger = logging.getLogger(__name__)


# --- A2A response shape (minimal) ------------------------------------------
# A2A v1.0 wire nests parts under Message / Task. We keep a flat, minimal shape
# sufficient for the internal mesh MVP: either a completed Task or an agent
# Message. ponytail: do not model the full A2A task state machine here (defer,
# ADR-5) — send is synchronous and always completes (or raises).
class TextPart(BaseModel):
    text: str
    type: str = "text"


class Message(BaseModel):
    """Minimal A2A agent message (role 'agent')."""

    role: str = "agent"
    parts: list[TextPart]
    message_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:16])


class Task(BaseModel):
    """Minimal A2A task (MVP always state='completed')."""

    state: str = "completed"
    result: Message


class LocalTransport:
    """In-process A2A transport: message/send → build_native_agent(target).run().

    Construct once (no resources held between calls). ``send`` is stateless: each
    call builds a fresh consumed agent (fresh turn, no message_history per
    ADR-5 MVP) and closes its emitter before returning.
    """

    def __init__(self, registry: Any | None = None) -> None:
        # registry injected for testability; runtime reads src.services._state.
        self._registry = registry

    def _resolve_registry(self) -> Any:
        if self._registry is not None:
            return self._registry
        from src.services import _state
        return _state.agent_registry

    async def send(self, target_agent_id: str, message: str) -> Message:
        """Run a fresh turn against the target agent, in-process.

        ADR-4: consumed agent = peer, full capability stack, scoped to its own
        agent_id (``agent_id_for_scope=target_agent_id``). ADR-5: zero network,
        synchronous send → completed.

        Raises ``KeyError`` if the target is not in the registry / catalog.
        """
        from src.harness.native_agent import HARNESS_TYPE, build_native_agent
        from src.harness.routes import assemble_capabilities
        from src.harness.emit import ObserveEmitter

        registry = self._resolve_registry()
        spec = registry.get(target_agent_id) if registry else None
        if spec is None:
            raise KeyError(
                f"a2a LocalTransport: target agent not found: {target_agent_id!r}"
            )
        spec_id = normalize_agent_id(spec.id)

        # Build consumed-agent resources (mirror _build_native_session shape),
        # but scope memory to the TARGET agent_id (ADR-4 core invariant).
        a2a_call_id = f"a2a_{uuid.uuid4().hex[:8]}"
        harness_id = f"a2a_{spec_id}_{a2a_call_id[-8:]}"
        emitter = ObserveEmitter(
            HARNESS_TYPE, harness_id=harness_id, session_id=a2a_call_id
        )
        try:
            await emitter.connect()  # best-effort (ADR-7)
        except Exception:
            logger.warning("a2a consumed emitter connect failed (%s)", harness_id)

        repo_root = os.getenv("AO2_REPO_ROOT", os.getcwd())
        cwd_scope = registry.resolve_cwd_scope(spec, repo_root=repo_root)
        session_key = f"agent-os-v2:{a2a_call_id}"

        caps, model_name = assemble_capabilities(
            spec,
            agent_id_for_scope=spec_id,  # ADR-4: target scope, NOT caller
            session_id=a2a_call_id,
            harness_id=harness_id,
            emitter=emitter,
            session_key=session_key,
            cwd_scope=cwd_scope,
        )
        # ADR-4 peer parity: carry global MCP servers like _build_native_session
        # so the consumed agent has the same tool surface as a harness session.
        from src.harness.mcp_config import load_global_mcp_servers
        agent = build_native_agent(
            capabilities=caps,
            instructions=spec.instructions or "",  # P2 真 bug:consumed agent system prompt
            model_settings={
                "anthropic_cache_instructions": "5m",
                "anthropic_cache_tool_definitions": "5m",
            },
            mcp_servers=load_global_mcp_servers() or None,
            model_name=model_name,
        )
        # Fresh turn (ADR-5 MVP): no message_history.
        result = await agent.run(message)
        try:
            await emitter.close()
        except Exception:
            pass
        text = result.output if isinstance(result.output, str) else str(result.output)
        return Message(parts=[TextPart(text=text)])


__all__ = ["LocalTransport", "Message", "Task", "TextPart"]
