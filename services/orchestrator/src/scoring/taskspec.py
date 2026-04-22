"""TaskSpec — standardized task definition for Sideline Committee members."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any


@dataclass
class TaskSpec:
    """
    Standardized task specification for Sideline Committee agents.

    Every Committee member (Transcriber, Refiner, Architect)
    conforms to this schema.
    """
    role: str  # "transcriber" | "refiner" | "architect"
    version: str = "1.0"

    # Trigger condition
    trigger_on: str = "scoring_signal"  # "scoring_signal" | "event" | "periodic"

    # Input/output schemas (JSON schema dicts)
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=dict)

    # LLM role
    llm_role: str = "none"  # "explain" | "validate" | "none"

    # Negotiation protocol
    negotiation: str = "single"  # "single" | "vote" | "consensus"

    # Routing
    routing: dict[str, str] = field(default_factory=dict)  # output_field -> destination

    # Metadata
    description: str = ""
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "role": self.role,
            "version": self.version,
            "trigger_on": self.trigger_on,
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
            "llm_role": self.llm_role,
            "negotiation": self.negotiation,
            "routing": self.routing,
            "description": self.description,
            "tags": self.tags,
        }


# ── Concrete TaskSpecs for each role ──────────────────────────────────────────


TRANSCRIBER_SPEC = TaskSpec(
    role="transcriber",
    version="1.0",
    trigger_on="scoring_signal",
    input_schema={
        "type": "object",
        "properties": {
            "action_units": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "timestamp": {"type": "string"},
                        "user_intent": {"type": "string"},
                        "tool_calls": {"type": "array"},
                        "tool_results": {"type": "array"},
                    },
                    "required": ["id", "timestamp", "user_intent"],
                },
            },
            "scoring_signal": {"type": "object"},  # ScoringSignal
        },
        "required": ["action_units"],
    },
    output_schema={
        "type": "object",
        "properties": {
            "relevant_facts": {"type": "array"},
            "discarded_facts": {"type": "array"},
            "extraction_metadata": {
                "type": "object",
                "properties": {
                    "total_action_units": {"type": "integer"},
                    "relevant_count": {"type": "integer"},
                    "discarded_count": {"type": "integer"},
                },
            },
        },
        "required": ["relevant_facts", "discarded_facts"],
    },
    llm_role="explain",
    negotiation="single",
    routing={
        "relevant_facts": "experience_kg.write_bulk",
        "discarded_facts": "layer1_kg.write",
    },
    description="Extract relevant facts from ActionUnits for L2 KG",
    tags=["extraction", "l1_to_l2"],
)


REFINER_SPEC = TaskSpec(
    role="refiner",
    version="1.0",
    trigger_on="scoring_signal",
    input_schema={
        "type": "object",
        "properties": {
            "nodes": {"type": "array"},  # ExperienceNodes
            "scoring_signal": {"type": "object"},  # ScoringSignal
        },
        "required": ["nodes"],
    },
    output_schema={
        "type": "object",
        "properties": {
            "new_skill_bundles": {"type": "array"},
            "retired_bundles": {"type": "array"},
            "merge_decisions": {"type": "array"},
        },
    },
    llm_role="explain",
    negotiation="vote",
    routing={
        "new_skill_bundles": "experience_kg.create_skill_bundle",
        "merge_decisions": "experience_kg.merge_skill_bundles",
    },
    description="Refine high-reuse nodes into skill bundles",
    tags=["refinement", "skill_bundle"],
)


ARCHITECT_SPEC = TaskSpec(
    role="architect",
    version="1.0",
    trigger_on="scoring_signal",
    input_schema={
        "type": "object",
        "properties": {
            "nodes": {"type": "array"},  # ExperienceNodes grouped by domain
            "domain": {"type": "string"},
            "scoring_signal": {"type": "object"},  # ScoringSignal
        },
        "required": ["nodes", "domain"],
    },
    output_schema={
        "type": "object",
        "properties": {
            "new_profiles": {"type": "array"},  # BaseProfileBundle
            "updated_profiles": {"type": "array"},
            "retired_profiles": {"type": "array"},
        },
    },
    llm_role="explain",
    negotiation="vote",
    routing={
        "new_profiles": "profile_registry.register",
        "updated_profiles": "profile_registry.update",
    },
    description="Compile domain nodes into BaseProfile bundles for meta agent",
    tags=["refinement", "baseprofile"],
)