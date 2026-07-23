"""AO2 internal A2A mesh (ADR: docs/adr/a2a-internal-mesh.md).

Internal catalog first; public ``/.well-known`` deferred (ADR-1). Cards project
from AgentSpec (declarative config), NOT from pydantic-ai Agent objects (ADR-3).
"""
from .card import AgentCard, AgentSkill, CardCapabilities, spec_to_card

__all__ = ["AgentCard", "AgentSkill", "CardCapabilities", "spec_to_card"]
