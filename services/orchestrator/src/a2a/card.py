"""A2A AgentCard model + AgentSpec -> AgentCard projection.

A2A spec field names used verbatim (protocolVersion, pushNotifications,
streaming). LocalTransport needs no url -> left None.

This is a deliberately FLATTENED, internal-only card shape (ADR-1: internal
mesh first, public /.well-known deferred). The normative v1.0 AgentCard nests
protocolVersion/url under supported_interfaces[]; we keep them flat because
the internal LocalTransport has exactly one interface and no public endpoint.
Do not expose this card over /.well-known — it is not v1.0-wire-compatible.

Projection is COARSE-grained on purpose (ADR-3): skills are derived from the
agent's role (spec.name) + declared skill ids, NOT 1:1 from pydantic-ai tools.
A2A does not describe internal tools. Capabilities default all-False (MVP is
synchronous send -> completed).
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from agent.agent_spec import AgentSpec

# A2A protocol version this card shape conforms to. Bump on a breaking change.
_PROTOCOL_VERSION = "0.3"


class CardCapabilities(BaseModel):
    """A2A capabilities block. MVP sync -> all False.

    Fields follow the normative A2A AgentCapabilities (streaming,
    pushNotifications). stateTransition is intentionally absent — it is not a
    real A2A capability field; task-state lifecycle, if needed later, goes via
    AgentExtension.
    """

    streaming: bool = False
    pushNotifications: bool = False


class AgentSkill(BaseModel):
    """Coarse-grained A2A skill (NOT a pydantic-ai tool)."""

    id: str
    name: str
    description: str
    tags: list[str] = []


class AgentCard(BaseModel):
    """A2A-shaped agent discovery card (internal catalog)."""

    name: str
    description: str
    version: str
    protocolVersion: str = _PROTOCOL_VERSION
    capabilities: CardCapabilities = Field(default_factory=CardCapabilities)
    skills: list[AgentSkill] = []
    url: str | None = None  # LocalTransport; None = no public endpoint


def _derive_skills(spec: AgentSpec) -> list[AgentSkill]:
    """Derive coarse A2A skills from role + declared skill ids.

    NEVER 1:1 from pydantic-ai tools. First skill describes the agent's primary
    role; each declared AO2 skill id becomes at most one coarse entry.
    """
    role_name = spec.name or spec.id
    skills: list[AgentSkill] = [
        AgentSkill(
            id=f"{spec.id}-role",
            name=role_name,
            description=(
                spec.instructions[:160]
                if spec.instructions
                else f"{role_name} agent"
            ),
            tags=["role", spec.id],
        )
    ]
    for sid in spec.skills:
        skills.append(
            AgentSkill(
                id=sid,
                name=sid,
                description=f"{role_name} {sid} capability",
                tags=["skill", sid],
            )
        )
    # ponytail: cap small — A2A cards advertise capabilities, not an exhaustive
    # tool inventory. Raise the cap when we genuinely advertise more.
    return skills[:4]


def spec_to_card(spec: AgentSpec, version: str = "0.1.0") -> AgentCard:
    """Project a declarative AgentSpec into an A2A AgentCard.

    Per ADR-3 reads only AgentSpec fields (name/instructions/skills), never the
    pydantic-ai Agent object. url None (LocalTransport; ADR-1 internal).
    """
    name = spec.name or spec.id
    description = spec.instructions[:280] if spec.instructions else f"{name} agent"
    return AgentCard(
        name=name,
        description=description,
        version=version,
        capabilities=CardCapabilities(),
        skills=_derive_skills(spec),
        url=None,
    )
