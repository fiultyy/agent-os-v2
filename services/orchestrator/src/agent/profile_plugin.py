"""ProfilePlugin - plugin protocol and built-in plugins for AgentBaseProfile.

Profile plugins allow customizing Agent behavior through a lifecycle-based hook system.
They are loaded dynamically and can be swapped at runtime.
"""
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from .profile import AgentBaseProfile


@runtime_checkable
class ProfilePlugin(Protocol):
    """Plugin protocol for Agent Base Profile customization.

    Implement this protocol to create custom plugins that hook into
    the profile lifecycle: load, compile, and switch.

    Methods:
        on_load: Called when a profile is first loaded for an agent.
        on_compile: Called during System Prompt compilation (pre-return).
        on_switch: Called when an active profile is being replaced.
    """

    def on_load(self, agent_id: str) -> "AgentBaseProfile":
        """Create or return an initial profile for an agent.

        Args:
            agent_id: The agent identifier requesting a profile.

        Returns:
            An AgentBaseProfile for the agent.
        """
        ...

    def on_compile(self, profile: "AgentBaseProfile") -> str:
        """Intercept or modify System Prompt compilation.

        Args:
            profile: The profile being compiled.

        Returns:
            The compiled system prompt string.
        """
        ...

    def on_switch(
        self, old_profile: "AgentBaseProfile", new_profile: "AgentBaseProfile"
    ) -> None:
        """Handle profile transition for an agent.

        Called when switching from one active profile to another.
        Use for cleanup, state transfer, or side-effects.

        Args:
            old_profile: The previously active profile (may be None).
            new_profile: The newly activated profile.
        """
        ...


class DefaultPlugins:
    """Collection of built-in default plugins for common agent types."""

    @staticmethod
    def general_plugin(agent_id: str) -> "AgentBaseProfile":
        """General-purpose assistant profile.

        Minimal L0 identity only. Suitable as a fallback or base template.

        Args:
            agent_id: The agent identifier.

        Returns:
            A minimal general-purpose AgentBaseProfile.
        """
        from .profile import AgentBaseProfile, LayerProfile

        profile = AgentBaseProfile(agent_id=agent_id)
        profile.add_layer(LayerProfile(
            layer=0,
            source="model_identity",
            content="You are a helpful AI assistant. "
                   "Provide accurate, concise, and practical responses.",
            priority=100,
            tags=["general", "default"],
        ))
        return profile

    @staticmethod
    def coding_plugin(agent_id: str) -> "AgentBaseProfile":
        """Coding-specialized assistant profile.

        Optimized for software development tasks with coding-specific guidelines.

        Args:
            agent_id: The agent identifier.

        Returns:
            A coding-specialized AgentBaseProfile.
        """
        from .profile import AgentBaseProfile, LayerProfile

        profile = AgentBaseProfile(agent_id=agent_id)
        profile.add_layer(LayerProfile(
            layer=0,
            source="model_identity",
            content="You are an expert coding assistant.",
            priority=100,
            tags=["coding", "specialized"],
        ))
        profile.add_layer(LayerProfile(
            layer=2,
            source="coding_rules",
            content=(
                "Before writing code: understand the requirements, identify the scope, "
                "and plan your approach.\n"
                "During coding: write clean, readable code with appropriate comments.\n"
                "After coding: verify the implementation matches requirements.\n"
                "When errors occur: analyze the error message carefully, "
                "check the relevant code section, and fix the root cause.\n"
                "Commit often with clear, descriptive commit messages."
            ),
            priority=90,
            tags=["coding", "rules"],
        ))
        return profile

    @staticmethod
    def researcher_plugin(agent_id: str) -> "AgentBaseProfile":
        """Research-oriented assistant profile.

        Optimized for information gathering, analysis, and synthesis.

        Args:
            agent_id: The agent identifier.

        Returns:
            A research-oriented AgentBaseProfile.
        """
        from .profile import AgentBaseProfile, LayerProfile

        profile = AgentBaseProfile(agent_id=agent_id)
        profile.add_layer(LayerProfile(
            layer=0,
            source="model_identity",
            content="You are a research assistant with broad knowledge "
                   "across multiple domains.",
            priority=100,
            tags=["research", "specialized"],
        ))
        profile.add_layer(LayerProfile(
            layer=2,
            source="research_guidelines",
            content=(
                "When researching: gather information from multiple sources, "
                "verify credibility, and note conflicting perspectives.\n"
                "When synthesizing: clearly distinguish facts from opinions, "
                "cite sources, and acknowledge uncertainty.\n"
                "When presenting: organize findings logically, "
                "use appropriate detail level, and highlight key insights."
            ),
            priority=90,
            tags=["research", "guidelines"],
        ))
        return profile


class PluginRegistry:
    """Registry for managing available ProfilePlugin instances.

    Provides a centralized way to register, retrieve, and enumerate
    available plugins at runtime.
    """

    def __init__(self):
        self._plugins: dict[str, type | ProfilePlugin] = {}

    def register(self, name: str, plugin: type | ProfilePlugin) -> None:
        """Register a plugin under a name.

        Args:
            name: Human-readable plugin identifier.
            plugin: Either a plugin class (instantiated on use) or a plugin instance.
        """
        self._plugins[name] = plugin

    def get(self, name: str) -> ProfilePlugin | None:
        """Retrieve a registered plugin by name.

        Args:
            name: The plugin identifier.

        Returns:
            A plugin instance, or None if not found.
        """
        entry = self._plugins.get(name)
        if entry is None:
            return None
        # Check for class/type FIRST (before ProfilePlugin isinstance).
        # isinstance(AClass, ProfileProtocol) can be True via __subclasshook__,
        # but we still need to instantiate it.
        if isinstance(entry, type):
            if issubclass(entry, ProfilePlugin):
                try:
                    return entry()  # type: ignore[abstract]
                except TypeError:
                    return entry  # type: ignore[return-value]
            # It's a type but not a ProfilePlugin — return as-is (callable)
            return entry  # type: ignore[return-value]
        if isinstance(entry, ProfilePlugin):
            return entry
        if callable(entry):
            # It's a callable (function or staticmethod) — return as-is;
            # create_profile decides how to invoke it.
            return entry  # type: ignore[return-value]
        return None

    def list_plugins(self) -> list[str]:
        """List all registered plugin names."""
        return sorted(self._plugins.keys())

    def create_profile(self, name: str, agent_id: str) -> "AgentBaseProfile | None":
        """Convenience method to create a profile via a named plugin.

        Args:
            name: Plugin name.
            agent_id: Target agent identifier.

        Returns:
            The created AgentBaseProfile, or None if plugin not found.
        """
        import inspect
        entry = self._plugins.get(name)
        if entry is None:
            return None
        # Check class/type first: @runtime_checkable Protocol makes
        # isinstance(AClass, Protocol) True even for the class itself.
        if inspect.isclass(entry):
            if issubclass(entry, ProfilePlugin):
                try:
                    instance = entry()
                    return instance.on_load(agent_id)
                except TypeError:
                    return None
            return None
        if isinstance(entry, ProfilePlugin):
            return entry.on_load(agent_id)
        if callable(entry):
            return entry(agent_id)  # type: ignore[operator]
        return None


# Global plugin registry instance
_global_registry: PluginRegistry | None = None


def get_plugin_registry() -> PluginRegistry:
    """Get the global PluginRegistry instance.

    Initializes the global registry with default plugins on first call.

    Returns:
        The global PluginRegistry.
    """
    global _global_registry
    if _global_registry is None:
        _global_registry = PluginRegistry()
        _global_registry.register("general", DefaultPlugins.general_plugin)
        _global_registry.register("coding", DefaultPlugins.coding_plugin)
        _global_registry.register("researcher", DefaultPlugins.researcher_plugin)
    return _global_registry
