"""Unit tests for D-14 Agent Base Profile (L0-L5 Layer Stack).

Tests cover:
- LayerProfile and AgentBaseProfile data models
- ProfileRegistry registration and file loading
- ProfilePlugin protocol and DefaultPlugins
- SOUL.md → L0 and AGENTS.md → L1+L2 parsing
- Profile serialization (to_dict / from_dict)
- Runtime profile switching
- Compilation produces different outputs for different profiles
"""
import json
import tempfile
import os
from pathlib import Path

import pytest

from agent.profile import AgentBaseProfile, LayerProfile
from agent.profile_registry import ProfileRegistry
from agent.profile_plugin import (
    DefaultPlugins,
    PluginRegistry,
    get_plugin_registry,
)


# =============================================================================
# LayerProfile Tests
# =============================================================================

class TestLayerProfile:
    def test_create_minimal(self):
        lp = LayerProfile(layer=0, source="test", content="Hello")
        assert lp.layer == 0
        assert lp.source == "test"
        assert lp.content == "Hello"
        assert lp.priority == 0
        assert lp.ttl_seconds is None
        assert lp.tags == []

    def test_create_full(self):
        lp = LayerProfile(
            layer=3, source="tool_desc", content="Tool content",
            priority=50, ttl_seconds=3600, tags=["tool", "beta"]
        )
        assert lp.layer == 3
        assert lp.source == "tool_desc"
        assert lp.content == "Tool content"
        assert lp.priority == 50
        assert lp.ttl_seconds == 3600
        assert lp.tags == ["tool", "beta"]

    def test_to_dict_from_dict_roundtrip(self):
        lp = LayerProfile(
            layer=2, source="coding", content="Plan first.",
            priority=10, ttl_seconds=7200, tags=["rule"]
        )
        restored = LayerProfile.from_dict(lp.to_dict())
        assert restored.layer == lp.layer
        assert restored.source == lp.source
        assert restored.content == lp.content
        assert restored.priority == lp.priority
        assert restored.ttl_seconds == lp.ttl_seconds
        assert restored.tags == lp.tags


# =============================================================================
# AgentBaseProfile Tests
# =============================================================================

class TestAgentBaseProfile:
    def test_create_empty_profile(self):
        profile = AgentBaseProfile(agent_id="test-agent")
        assert profile.agent_id == "test-agent"
        # All 6 layers should exist (L0-L5)
        assert len(profile.layers) == 6
        for i in range(6):
            assert i in profile.layers
            assert profile.layers[i] == []

    def test_add_layer_single(self):
        profile = AgentBaseProfile(agent_id="test-agent")
        profile.add_layer(LayerProfile(layer=0, source="soul", content="I am a bot."))
        assert len(profile.layers[0]) == 1
        assert profile.layers[0][0].content == "I am a bot."

    def test_add_layer_priority_order(self):
        """Higher priority should come first within the same layer."""
        profile = AgentBaseProfile(agent_id="test-agent")
        profile.add_layer(LayerProfile(layer=0, source="low", content="Low", priority=10))
        profile.add_layer(LayerProfile(layer=0, source="high", content="High", priority=100))
        profile.add_layer(LayerProfile(layer=0, source="mid", content="Mid", priority=50))

        assert profile.layers[0][0].source == "high"
        assert profile.layers[0][1].source == "mid"
        assert profile.layers[0][2].source == "low"

    def test_add_layer_different_layers(self):
        profile = AgentBaseProfile(agent_id="test-agent")
        profile.add_layer(LayerProfile(layer=0, source="l0", content="L0"))
        profile.add_layer(LayerProfile(layer=1, source="l1", content="L1"))
        profile.add_layer(LayerProfile(layer=4, source="l4", content="L4"))

        assert len(profile.layers[0]) == 1
        assert len(profile.layers[1]) == 1
        assert len(profile.layers[4]) == 1
        assert profile.layers[2] == []  # Unused layer

    def test_compile_excludes_l5(self):
        """L5 (History) should NOT be included in compile() output."""
        profile = AgentBaseProfile(agent_id="test-agent")
        profile.add_layer(LayerProfile(layer=0, source="l0", content="L0 content"))
        profile.add_layer(LayerProfile(layer=5, source="history", content="History content"))

        compiled = profile.compile()
        assert "L0 content" in compiled
        assert "History content" not in compiled
        assert "=== l0 (L0) ===" in compiled

    def test_compile_format(self):
        profile = AgentBaseProfile(agent_id="test-agent")
        profile.add_layer(LayerProfile(layer=0, source="soul_md", content="I am a bot."))
        profile.add_layer(LayerProfile(layer=2, source="agents_md", content="Follow rules."))

        compiled = profile.compile()
        lines = compiled.split("\n")
        assert "=== soul_md (L0) ===" in lines
        assert "I am a bot." in compiled
        assert "=== agents_md (L2) ===" in compiled
        assert "Follow rules." in compiled

    def test_compile_different_profiles_different_output(self):
        """Two profiles with different layers should produce different compiled output."""
        profile1 = AgentBaseProfile(agent_id="a1")
        profile1.add_layer(LayerProfile(layer=0, source="id", content="I am Agent A."))

        profile2 = AgentBaseProfile(agent_id="a2")
        profile2.add_layer(LayerProfile(layer=0, source="id", content="I am Agent B."))

        assert profile1.compile() != profile2.compile()
        assert "Agent A" in profile1.compile()
        assert "Agent B" in profile2.compile()

    def test_get_layer(self):
        profile = AgentBaseProfile(agent_id="test-agent")
        profile.add_layer(LayerProfile(layer=2, source="r1", content="R1"))
        profile.add_layer(LayerProfile(layer=2, source="r2", content="R2"))

        layer2 = profile.get_layer(2)
        assert len(layer2) == 2

        layer3 = profile.get_layer(3)
        assert layer3 == []

    def test_remove_by_source(self):
        profile = AgentBaseProfile(agent_id="test-agent")
        profile.add_layer(LayerProfile(layer=0, source="soul", content="Soul content"))
        profile.add_layer(LayerProfile(layer=2, source="soul", content="Soul rules"))
        profile.add_layer(LayerProfile(layer=1, source="other", content="Other"))

        count = profile.remove_by_source("soul")
        assert count == 2
        assert profile.layers[0] == []
        assert profile.layers[1][0].source == "other"

    def test_serialization_roundtrip(self):
        profile = AgentBaseProfile(agent_id="test-agent")
        profile.add_layer(LayerProfile(layer=0, source="soul", content="Soul content", priority=50))
        profile.add_layer(LayerProfile(layer=2, source="agents", content="Agents content", tags=["dev"]))

        data = profile.to_dict()
        restored = AgentBaseProfile.from_dict(data)

        assert restored.agent_id == "test-agent"
        assert len(restored.layers[0]) == 1
        assert restored.layers[0][0].content == "Soul content"
        assert restored.layers[2][0].tags == ["dev"]

    def test_copy_is_independent(self):
        """Copy should be a deep copy, not a reference."""
        profile = AgentBaseProfile(agent_id="test-agent")
        profile.add_layer(LayerProfile(layer=0, source="s", content="Original"))

        copy = profile.copy()
        copy.layers[0][0].content = "Modified"

        assert profile.layers[0][0].content == "Original"


# =============================================================================
# ProfileRegistry Tests
# =============================================================================

class TestProfileRegistry:
    def test_register_and_get(self):
        registry = ProfileRegistry()
        profile = AgentBaseProfile(agent_id="agent-1")
        profile.add_layer(LayerProfile(layer=0, source="test", content="Test"))

        registry.register(profile)
        retrieved = registry.get("agent-1")

        assert retrieved is not None
        assert retrieved.agent_id == "agent-1"
        assert retrieved.layers[0][0].content == "Test"

    def test_get_nonexistent(self):
        registry = ProfileRegistry()
        assert registry.get("nonexistent") is None

    def test_list_profiles(self):
        registry = ProfileRegistry()
        p1 = AgentBaseProfile(agent_id="agent-a")
        p2 = AgentBaseProfile(agent_id="agent-b")
        registry.register(p1)
        registry.register(p2)

        listed = registry.list_profiles()
        assert listed == ["agent-a", "agent-b"]

    def test_unregister(self):
        registry = ProfileRegistry()
        registry.register(AgentBaseProfile(agent_id="agent-1"))
        assert registry.get("agent-1") is not None

        removed = registry.unregister("agent-1")
        assert removed is True
        assert registry.get("agent-1") is None

        # Unregister non-existent should return False
        assert registry.unregister("nonexistent") is False

    def test_get_returns_copy(self):
        """get() should return a copy, not the stored reference."""
        registry = ProfileRegistry()
        profile = AgentBaseProfile(agent_id="agent-1")
        profile.add_layer(LayerProfile(layer=0, source="s", content="Original"))
        registry.register(profile)

        retrieved = registry.get("agent-1")
        retrieved.layers[0][0].content = "Modified"

        stored = registry.get("agent-1")
        assert stored.layers[0][0].content == "Original"


class TestProfileRegistryFileLoading:
    def test_load_from_files_soul_only(self):
        """Test loading when only SOUL.md exists."""
        with tempfile.TemporaryDirectory() as tmpdir:
            soul_path = Path(tmpdir) / "SOUL.md"
            soul_path.write_text("I am a helpful assistant named Claw.", encoding="utf-8")

            registry = ProfileRegistry()
            profile = registry.load_from_files("agent-soul", tmpdir)

            assert profile.agent_id == "agent-soul"
            l0_layers = profile.get_layer(0)
            assert len(l0_layers) == 1
            assert l0_layers[0].source == "soul_md"
            assert "helpful assistant" in l0_layers[0].content

    def test_load_from_files_agents_only(self):
        """Test loading when only AGENTS.md exists."""
        with tempfile.TemporaryDirectory() as tmpdir:
            agents_path = Path(tmpdir) / "AGENTS.md"
            agents_path.write_text("# Agent Rules\n\nBe helpful. Be accurate.", encoding="utf-8")

            registry = ProfileRegistry()
            profile = registry.load_from_files("agent-rules", tmpdir)

            # Should have L1 content
            l1_layers = profile.get_layer(1)
            assert len(l1_layers) == 1
            assert l1_layers[0].source == "agents_md_identity"
            assert "Agent Rules" in l1_layers[0].content

    def test_load_from_files_both(self):
        """Test loading with both SOUL.md and AGENTS.md."""
        with tempfile.TemporaryDirectory() as tmpdir:
            soul_path = Path(tmpdir) / "SOUL.md"
            soul_path.write_text("I am Project Expert.", encoding="utf-8")

            agents_path = Path(tmpdir) / "AGENTS.md"
            agents_path.write_text(
                "# Identity\nI am a project tracking assistant.\n"
                "# Guidelines\nAlways verify before acting.",
                encoding="utf-8"
            )

            registry = ProfileRegistry()
            profile = registry.load_from_files("agent-full", tmpdir)

            # L0 from SOUL.md
            l0 = profile.get_layer(0)
            assert len(l0) == 1
            assert "Project Expert" in l0[0].content

            # L1 from AGENTS.md (first section)
            l1 = profile.get_layer(1)
            assert len(l1) == 1
            assert "Identity" in l1[0].content

            # L2 from AGENTS.md (remaining sections)
            l2 = profile.get_layer(2)
            assert len(l2) == 1
            assert "Guidelines" in l2[0].content

    def test_load_from_files_neither_exists(self):
        """When neither file exists, should return empty profile."""
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = ProfileRegistry()
            profile = registry.load_from_files("agent-empty", tmpdir)

            for layer in range(5):
                assert profile.get_layer(layer) == []

    def test_load_from_files_empty_content(self):
        """Empty file should be skipped."""
        with tempfile.TemporaryDirectory() as tmpdir:
            soul_path = Path(tmpdir) / "SOUL.md"
            soul_path.write_text("   \n\t\n   ", encoding="utf-8")

            registry = ProfileRegistry()
            profile = registry.load_from_files("agent-empty2", tmpdir)

            assert profile.get_layer(0) == []


class TestProfileRegistryPersistence:
    def test_save_and_load_file(self):
        """Test save_to_file / load_from_file roundtrip."""
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = ProfileRegistry()
            profile = AgentBaseProfile(agent_id="agent-persist")
            profile.add_layer(LayerProfile(
                layer=0, source="soul", content="Persisted content", priority=50
            ))
            registry.register(profile)

            file_path = Path(tmpdir) / "profile.json"
            registry.save_to_file("agent-persist", file_path)

            loaded = registry.load_from_file(file_path)
            assert loaded.agent_id == "agent-persist"
            assert loaded.layers[0][0].content == "Persisted content"
            assert loaded.layers[0][0].priority == 50


# =============================================================================
# ProfilePlugin Tests
# =============================================================================

class TestDefaultPlugins:
    def test_general_plugin(self):
        profile = DefaultPlugins.general_plugin("test-agent")

        assert profile.agent_id == "test-agent"
        l0 = profile.get_layer(0)
        assert len(l0) == 1
        assert "helpful ai assistant" in l0[0].content.lower()

    def test_coding_plugin(self):
        profile = DefaultPlugins.coding_plugin("coder-1")

        l0 = profile.get_layer(0)
        assert any("coding" in p.content.lower() for p in l0)

        l2 = profile.get_layer(2)
        assert len(l2) == 1
        assert "plan" in l2[0].content.lower()

    def test_researcher_plugin(self):
        profile = DefaultPlugins.researcher_plugin("researcher-1")

        l0 = profile.get_layer(0)
        assert len(l0) == 1
        assert "research" in l0[0].content.lower() or "knowledge" in l0[0].content.lower()

    def test_different_plugins_produce_different_prompts(self):
        """General vs Coding vs Researcher should produce different compiled prompts."""
        gen = DefaultPlugins.general_plugin("g")
        cod = DefaultPlugins.coding_plugin("c")
        res = DefaultPlugins.researcher_plugin("r")

        gen_compiled = gen.compile()
        cod_compiled = cod.compile()
        res_compiled = res.compile()

        # All should be different from each other
        assert gen_compiled != cod_compiled
        assert cod_compiled != res_compiled
        assert gen_compiled != res_compiled


class TestPluginRegistry:
    def test_register_and_get(self):
        registry = PluginRegistry()
        registry.register("test", DefaultPlugins.general_plugin)

        plugin = registry.get("test")
        assert plugin is not None

    def test_list_plugins(self):
        registry = PluginRegistry()
        registry.register("a", DefaultPlugins.general_plugin)
        registry.register("b", DefaultPlugins.coding_plugin)

        names = registry.list_plugins()
        assert "a" in names
        assert "b" in names

    def test_create_profile_via_plugin(self):
        registry = PluginRegistry()
        registry.register("gen", DefaultPlugins.general_plugin)

        profile = registry.create_profile("gen", "agent-via-plugin")
        assert profile is not None
        assert profile.agent_id == "agent-via-plugin"
        assert len(profile.get_layer(0)) == 1


class TestGlobalPluginRegistry:
    def test_get_plugin_registry_singleton(self):
        """get_plugin_registry should return the same instance."""
        r1 = get_plugin_registry()
        r2 = get_plugin_registry()
        assert r1 is r2

    def test_default_plugins_registered(self):
        registry = get_plugin_registry()
        names = registry.list_plugins()
        assert "general" in names
        assert "coding" in names
        assert "researcher" in names

    def test_default_plugins_create_valid_profiles(self):
        registry = get_plugin_registry()
        for name in ["general", "coding", "researcher"]:
            profile = registry.create_profile(name, f"agent-{name}")
            assert profile is not None
            assert profile.compile().strip() != ""


# =============================================================================
# Runtime Profile Switching Tests
# =============================================================================

class TestProfileSwitching:
    def test_switch_via_registry(self):
        """Profile switching via registry: get old, register new."""
        registry = ProfileRegistry()

        # Initial profile
        p1 = AgentBaseProfile(agent_id="switch-agent")
        p1.add_layer(LayerProfile(layer=0, source="id", content="Profile One"))
        registry.register(p1)

        # Switch to new profile
        p2 = AgentBaseProfile(agent_id="switch-agent")
        p2.add_layer(LayerProfile(layer=0, source="id", content="Profile Two"))
        registry.register(p2)

        current = registry.get("switch-agent")
        assert current.layers[0][0].content == "Profile Two"

    def test_on_switch_hook_called(self):
        """on_switch should be called when switching via plugin."""
        registry = ProfileRegistry()
        switches: list[tuple[str, str]] = []

        class TrackingPlugin:
            def on_load(self, agent_id: str) -> AgentBaseProfile:
                return DefaultPlugins.general_plugin(agent_id)

            def on_compile(self, profile: AgentBaseProfile) -> str:
                return profile.compile()

            def on_switch(
                self, old: AgentBaseProfile, new: AgentBaseProfile
            ) -> None:
                old_id = old.agent_id if old else "none"
                switches.append((old_id, new.agent_id))

        registry2 = PluginRegistry()
        registry2.register("track", TrackingPlugin)

        # First load
        profile1 = registry2.create_profile("track", "agent-switch")
        switches.clear()

        # Simulate switch
        profile2 = registry2.create_profile("track", "agent-switch")
        plugin = registry2.get("track")
        assert plugin is not None
        plugin.on_switch(profile1, profile2)

        assert len(switches) == 1
        assert switches[0][1] == "agent-switch"


# =============================================================================
# Module-level import test
# =============================================================================

class TestModuleImports:
    def test_all_exports_importable(self):
        """All key classes should be importable from agent module."""
        from agent.profile import AgentBaseProfile, LayerProfile
        from agent.profile_registry import ProfileRegistry
        from agent.profile_plugin import (
            DefaultPlugins,
            PluginRegistry,
            ProfilePlugin,
            get_plugin_registry,
        )

        assert AgentBaseProfile is not None
        assert LayerProfile is not None
        assert ProfileRegistry is not None
        assert DefaultPlugins is not None
        assert ProfilePlugin is not None
        assert PluginRegistry is not None
