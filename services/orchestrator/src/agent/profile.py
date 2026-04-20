"""Agent Base Profile - L0-L5 Layer Stack data models.

Layer Stack Architecture:
- L0: Model Identity (who the model is)
- L1: Agent Identity (SOUL.md → persona)
- L2: Operational Guidelines (AGENTS.md → rules)
- L3: Contextual Memory (project-specific context)
- L4: Tool & Resource definitions
- L5: Conversation History (managed separately)
"""
from dataclasses import dataclass, field, asdict
from typing import Self


@dataclass
class LayerProfile:
    """Single layer profile within an Agent's base profile.

    Attributes:
        layer: Layer index (0-5). L5 is reserved for history.
        source: Identifier for the source of this layer (e.g., 'soul', 'agents_md', 'tool_registry').
        content: The actual text/content for this layer.
        priority: Priority within the same layer (higher = earlier).
        ttl_seconds: Time-to-live in seconds. None means never expires.
        tags: Arbitrary tags for categorization and retrieval.
    """
    layer: int
    source: str
    content: str
    priority: int = 0
    ttl_seconds: int | None = None
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> Self:
        return cls(**data)


@dataclass
class AgentBaseProfile:
    """Agent Base Profile composed of multiple LayerProfiles (L0-L5).

    This is the core data structure for Agent's System Prompt compilation.
    Layers are stacked in order (L0 at bottom, L4 at top, L5=History separate).

    Attributes:
        agent_id: Unique identifier for this agent profile.
        layers: Dictionary mapping layer index to list of LayerProfiles.
    """
    agent_id: str
    layers: dict[int, list[LayerProfile]] = field(
        default_factory=lambda: {i: [] for i in range(6)}
    )

    def add_layer(self, layer_profile: LayerProfile) -> None:
        """Add a layer profile and maintain priority order within the layer."""
        layer = layer_profile.layer
        if layer not in self.layers:
            self.layers[layer] = []
        self.layers[layer].append(layer_profile)
        self.layers[layer].sort(key=lambda x: x.priority, reverse=True)

    def compile(self) -> str:
        """Compile all layers into a complete System Prompt string.

        Layers are concatenated in order from L0 to L4.
        L5 (History) is NOT included here - it's managed separately by ContextCompiler.

        Returns:
            Formatted system prompt string with layer markers.
        """
        lines = []
        for layer in range(5):  # L0-L4 only, L5 is history
            for profile in self.layers.get(layer, []):
                lines.append(f"=== {profile.source} (L{layer}) ===")
                lines.append(profile.content)
                lines.append("")
        return "\n".join(lines)

    def get_layer(self, layer: int) -> list[LayerProfile]:
        """Get all profiles for a specific layer."""
        return list(self.layers.get(layer, []))

    def get_all_sources(self) -> list[str]:
        """Get unique list of all source identifiers in this profile."""
        sources = set()
        for layer_profiles in self.layers.values():
            for p in layer_profiles:
                sources.add(p.source)
        return sorted(sources)

    def remove_by_source(self, source: str) -> int:
        """Remove all layers matching the given source.

        Returns:
            Number of layers removed.
        """
        count = 0
        for layer in self.layers:
            before = len(self.layers[layer])
            self.layers[layer] = [p for p in self.layers[layer] if p.source != source]
            count += before - len(self.layers[layer])
        return count

    def to_dict(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "layers": {
                str(k): [p.to_dict() for p in v]
                for k, v in self.layers.items()
            },
        }

    @classmethod
    def from_dict(cls, data: dict) -> Self:
        layers = {
            int(k): [LayerProfile.from_dict(p) for p in v]
            for k, v in data.get("layers", {}).items()
        }
        # Ensure all 6 layers exist
        for i in range(6):
            if i not in layers:
                layers[i] = []
        return cls(agent_id=data["agent_id"], layers=layers)

    def copy(self) -> Self:
        """Create a deep copy of this profile."""
        return AgentBaseProfile.from_dict(self.to_dict())
