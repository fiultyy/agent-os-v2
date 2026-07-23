"""Unit tests for A2A AgentCard projection + internal catalog (node A).

Covers: AgentSpec -> AgentCard validity, catalog completeness (native+main),
coarse-grained skills (NOT 1:1 tools), A2A field names, no external deps.
Sync tests, no loop wrapper (pure pydantic schema + projection).
"""
from agent.agent_registry import AgentRegistry
from agent.agent_spec import AgentSpec
from a2a.card import (
    AgentCard,
    AgentSkill,
    CardCapabilities,
    spec_to_card,
)


# -----------------------------------------------------------------------------
# projection validity
# -----------------------------------------------------------------------------
class TestSpecToCard:
    def test_native_spec_projects_valid_card(self):
        spec = AgentSpec(
            id="native",
            name="原生 harness 迭代专员",
            skills=["ao2-architecture"],
            instructions="short role blurb",
        )
        card = spec_to_card(spec)
        assert isinstance(card, AgentCard)
        assert card.name == "原生 harness 迭代专员"
        assert card.description == "short role blurb"
        assert card.version  # non-empty
        assert card.protocolVersion == "0.3"
        assert card.url is None  # LocalTransport
        assert isinstance(card.capabilities, CardCapabilities)
        # MVP sync -> all False
        assert not card.capabilities.streaming
        assert not card.capabilities.pushNotifications

    def test_main_spec_projects_valid_card(self):
        spec = AgentSpec(id="main", name="全能助理", skills=["ao2-architecture"])
        card = spec_to_card(spec)
        assert card.name == "全能助理"
        assert card.protocolVersion == "0.3"
        assert isinstance(card.skills, list) and card.skills

    def test_card_uses_required_a2a_field_names(self):
        """Field names must match A2A spec verbatim."""
        card = spec_to_card(AgentSpec(id="native"))
        dumped = card.model_dump()
        for required in (
            "name", "description", "version", "protocolVersion",
            "capabilities", "skills", "url",
        ):
            assert required in dumped, f"missing A2A field {required!r}"
        for cap_field in ("streaming", "pushNotifications"):
            assert cap_field in dumped["capabilities"], (
                f"missing capability {cap_field!r}"
            )
        # stateTransition is NOT a real A2A field — must be absent
        assert "stateTransition" not in dumped["capabilities"]
        for skill_field in ("id", "name", "description", "tags"):
            assert skill_field in dumped["skills"][0], (
                f"missing skill field {skill_field!r}"
            )

    def test_instructions_truncated_to_description(self):
        long = "x" * 500
        card = spec_to_card(AgentSpec(id="native", instructions=long))
        assert len(card.description) <= 280

    def test_no_instructions_falls_back_to_name(self):
        spec = AgentSpec(id="native", name="RoleX")
        card = spec_to_card(spec)
        assert "RoleX" in card.description


# -----------------------------------------------------------------------------
# coarse-grained skills (NOT 1:1 pydantic-ai tools)
# -----------------------------------------------------------------------------
class TestCoarseSkills:
    def test_skills_are_coarse_not_per_tool(self):
        """A2A card skills advertise capabilities, not pydantic-ai tools.

        A spec with several declared AO2 skill ids yields at most one entry per
        id + one role entry — never a fan-out of internal tools.
        """
        spec = AgentSpec(
            id="native",
            skills=["ao2-architecture", "qa-test"],
        )
        card = spec_to_card(spec)
        ids = [s.id for s in card.skills]
        # role entry + 2 declared skills = 3 coarse entries
        assert "native-role" in ids
        assert "ao2-architecture" in ids
        assert "qa-test" in ids
        assert len(card.skills) == 3

    def test_skills_capped_small(self):
        """ponytail: coarse cap, not an exhaustive tool inventory."""
        spec = AgentSpec(
            id="native", skills=[f"s{i}" for i in range(20)]
        )
        card = spec_to_card(spec)
        assert len(card.skills) <= 4

    def test_skill_entries_are_agent_skill_type(self):
        card = spec_to_card(AgentSpec(id="native", skills=["x"]))
        assert all(isinstance(s, AgentSkill) for s in card.skills)
        assert all(isinstance(s.tags, list) for s in card.skills)


# -----------------------------------------------------------------------------
# catalog completeness
# -----------------------------------------------------------------------------
class TestCatalog:
    def test_registry_lists_native_and_main(self, tmp_path, monkeypatch):
        yaml = tmp_path / "agents.yaml"
        yaml.write_text(
            """
defaults:
  model: glm-4.7
agents:
  - id: native
    default: true
    name: 原生 harness 迭代专员
    skills: [ao2-architecture]
  - id: main
    name: 全能助理
    skills: [ao2-architecture]
""",
            encoding="utf-8",
        )
        monkeypatch.setenv("AO2_REPO_ROOT", str(tmp_path))
        monkeypatch.setenv("AO2_STATE_DIR", str(tmp_path))
        reg = AgentRegistry.load(str(yaml))
        cards = dict(reg.list_cards())
        assert set(cards) == {"native", "main"}
        assert all(isinstance(c, AgentCard) for c in cards.values())

    def test_get_card_returns_card_or_none(self):
        reg = AgentRegistry()
        reg._fallback_native()
        card = reg.get_card("native")
        assert card is not None and card.name  # fallback spec has no name -> id
        assert reg.get_card("nope") is None

    def test_catalog_id_for_each_card_matches_agent_id(self, tmp_path, monkeypatch):
        yaml = tmp_path / "agents.yaml"
        yaml.write_text(
            "defaults: {model: m}\nagents:\n  - id: native\n    default: true\n"
            "  - id: main\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("AO2_REPO_ROOT", str(tmp_path))
        monkeypatch.setenv("AO2_STATE_DIR", str(tmp_path))
        reg = AgentRegistry.load(str(yaml))
        for aid, card in reg.list_cards():
            # role skill id is prefixed by agent id — proves projection keyed
            # off the correct spec
            assert any(s.id.startswith(aid) for s in card.skills)


# -----------------------------------------------------------------------------
# ADR-3: projection reads only AgentSpec, never a pydantic-ai Agent
# -----------------------------------------------------------------------------
def test_spec_to_card_signature_takes_only_agent_spec():
    """ADR-3 enforcement at the type level: spec_to_card must accept only
    AgentSpec, never a pydantic-ai Agent instance."""
    import inspect

    from agent.agent_spec import AgentSpec

    sig = inspect.signature(spec_to_card)
    annotation = sig.parameters["spec"].annotation
    # from __future__ import annotations -> annotation is the string "AgentSpec"
    assert annotation is AgentSpec or annotation == "AgentSpec", (
        f"spec_to_card must take AgentSpec, got {annotation!r}"
    )


# -----------------------------------------------------------------------------
# no external deps (AST-based: catches `from x import y`, not just `import x`)
# -----------------------------------------------------------------------------
def test_card_module_has_no_external_runtime_deps():
    """Pure stdlib + pydantic (already a project dep). AST scan catches both
    `import httpx` and `from httpx import Client` forms."""
    import ast
    from pathlib import Path

    src = Path(
        __import__("a2a", fromlist=["card"]).__file__
    ).parent.joinpath("card.py").read_text()
    tree = ast.parse(src)
    mods: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            mods.update(a.name.split(".")[0] for a in n.names)
        elif isinstance(n, ast.ImportFrom):
            if n.module:
                mods.add(n.module.split(".")[0])
    banned = {"httpx", "a2a", "requests", "flask", "fastapi", "aiohttp"}
    leaked = mods & banned
    assert not leaked, f"external deps leaked into a2a.card: {leaked}"
