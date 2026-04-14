"""
Unit tests for SkillLoader + SkillCatalog
"""

import pytest
import tempfile
import shutil
from pathlib import Path


class TestSkillLoader:
    """Tests for SkillLoader."""

    def _make_skill_md(self, name, description="", version="1.0.0",
                       visible=True, user_invocable=False,
                       tools=None, env_vars=None):
        tools = tools or []
        env_vars = env_vars or []
        return f"""---
name: {name}
description: {description}
version: {version}
requires:
  tools: {str(tools)}
  env: {str(env_vars)}
exposure:
  visible: {str(visible).lower()}
  user_invocable: {str(user_invocable).lower()}
---
# Skill body
"""

    def test_scan_empty_dir(self):
        """Empty directory yields no skills."""
        from agent_os_orchestrator.src.skills.skill_loader import SkillLoader
        with tempfile.TemporaryDirectory() as td:
            loader = SkillLoader(
                user_dir=Path(td),
                project_dir=Path("/nonexistent"),
                builtin_dir=Path("/nonexistent"),
            )
            results = loader.scan()
        assert results == []

    def test_scan_single_skill(self):
        """Single valid SKILL.md is discovered."""
        from agent_os_orchestrator.src.skills.skill_loader import SkillLoader
        with tempfile.TemporaryDirectory() as td:
            skill_dir = Path(td) / "test-skill"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text(
                self._make_skill_md("test-skill", "A test skill", "1.0.0"),
                encoding="utf-8",
            )
            loader = SkillLoader(
                user_dir=Path(td),
                project_dir=Path("/nonexistent"),
                builtin_dir=Path("/nonexistent"),
            )
            results = loader.scan()

        assert len(results) == 1
        assert results[0].name == "test-skill"
        assert results[0].description == "A test skill"
        assert results[0].version == "1.0.0"
        assert results[0].source == "user"

    def test_skips_dirs_without_skill_md(self):
        """Directories without SKILL.md are skipped."""
        from agent_os_orchestrator.src.skills.skill_loader import SkillLoader
        with tempfile.TemporaryDirectory() as td:
            bare_dir = Path(td) / "no-skill"
            bare_dir.mkdir()
            (bare_dir / "README.md").write_text("no skill here", encoding="utf-8")
            loader = SkillLoader(user_dir=Path(td))
            results = loader.scan()
        assert results == []

    def test_skips_dotfiles_and_skip_names(self):
        """Hidden directories and __pycache__ etc are skipped."""
        from agent_os_orchestrator.src.skills.skill_loader import SkillLoader
        with tempfile.TemporaryDirectory() as td:
            for bad in [".hidden", "__pycache__", "node_modules"]:
                (Path(td) / bad / "SKILL.md").parent.mkdir()
                (Path(td) / bad / "SKILL.md").write_text(
                    self._make_skill_md("bad-skill"), encoding="utf-8",
                )
            (Path(td) / "good-skill" / "SKILL.md").write_text(
                self._make_skill_md("good-skill", "Good"), encoding="utf-8",
            )
            loader = SkillLoader(user_dir=Path(td))
            results = loader.scan()
        names = {r.name for r in results}
        assert "good-skill" in names
        assert "bad-skill" not in names

    def test_high_priority_overwrites_low(self):
        """user > project > builtin priority."""
        from agent_os_orchestrator.src.skills.skill_loader import SkillLoader
        with tempfile.TemporaryDirectory() as td:
            # builtin has skill "my-skill"
            builtin = Path(td) / "builtin"
            builtin.mkdir()
            (builtin / "my-skill" / "SKILL.md").write_text(
                self._make_skill_md("my-skill", "Builtin version"),
                encoding="utf-8",
            )
            # user has same skill "my-skill" — should win
            user = Path(td) / "user"
            user.mkdir()
            (user / "my-skill" / "SKILL.md").write_text(
                self._make_skill_md("my-skill", "User version"),
                encoding="utf-8",
            )
            loader = SkillLoader(
                builtin_dir=builtin,
                user_dir=user,
                project_dir=Path("/nonexistent"),
            )
            results = loader.scan()

        assert len(results) == 1
        assert results[0].source == "user"
        assert results[0].description == "User version"

    def test_validate_path_blocks_escape(self):
        """Path traversal attempts are blocked."""
        from agent_os_orchestrator.src.skills.skill_loader import SkillLoader
        loader = SkillLoader()
        root = Path("/safe/root")
        # File outside root should be rejected
        bad = Path("/etc/passwd")
        assert loader._validate_path(bad, root) is False

    def test_exposure_fields_parsed(self):
        """exposure.visible and exposure.user_invocable are parsed."""
        from agent_os_orchestrator.src.skills.skill_loader import SkillLoader
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "secret" / "SKILL.md").write_text(
                self._make_skill_md("secret", "Hidden skill",
                                    visible=False, user_invocable=True),
                encoding="utf-8",
            )
            loader = SkillLoader(user_dir=Path(td))
            results = loader.scan()

        assert len(results) == 1
        assert results[0].exposure.visible is False
        assert results[0].exposure.user_invocable is True

    def test_requires_tools_and_env(self):
        """requires.tools and requires.env are parsed."""
        from agent_os_orchestrator.src.skills.skill_loader import SkillLoader
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "tooled" / "SKILL.md").write_text(
                self._make_skill_md("tooled", "Has requirements",
                                    tools=["browser", "http_get"],
                                    env_vars=["OPENAI_API_KEY"]),
                encoding="utf-8",
            )
            loader = SkillLoader(user_dir=Path(td))
            results = loader.scan()

        assert len(results) == 1
        assert "browser" in results[0].requires.tools
        assert "OPENAI_API_KEY" in results[0].requires.env


class TestSkillCatalog:
    """Tests for SkillCatalog."""

    def test_reload_populates_entries(self):
        """reload() scans and populates _entries."""
        from agent_os_orchestrator.src.skills.skill_loader import SkillLoader
        from agent_os_orchestrator.src.skills.skill_catalog import SkillCatalog

        with tempfile.TemporaryDirectory() as td:
            skill_dir = Path(td) / "my-skill"
            skill_dir.mkdir()
            skill_md = skill_dir / "SKILL.md"
            skill_md.write_text("""---
name: my-skill
description: My skill description
version: 2.0.0
requires:
  tools: []
  env: []
exposure:
  visible: true
  user_invocable: false
---
""", encoding="utf-8")
            loader = SkillLoader(user_dir=Path(td))
            catalog = SkillCatalog(loader)
            catalog.reload()

        assert "my-skill" in {e.name for e in catalog._entries.values()}
        entry = catalog.get("my-skill")
        assert entry is not None
        assert entry.version == "2.0.0"

    def test_get_returns_none_for_missing(self):
        """get() returns None for unknown skill."""
        from agent_os_orchestrator.src.skills.skill_catalog import SkillCatalog
        catalog = SkillCatalog()
        catalog.reload()
        assert catalog.get("nonexistent-skill-xyz") is None

    def test_list_all_excludes_invisible(self):
        """list_all() excludes skills with exposure.visible=False."""
        from agent_os_orchestrator.src.skills.skill_loader import SkillLoader
        from agent_os_orchestrator.src.skills.skill_catalog import SkillCatalog

        with tempfile.TemporaryDirectory() as td:
            visible_dir = Path(td) / "visible-skill"
            invisible_dir = Path(td) / "invisible-skill"
            visible_dir.mkdir()
            invisible_dir.mkdir()

            (visible_dir / "SKILL.md").write_text("""---
name: visible-skill
description: Shown in list
version: 1.0.0
requires:
  tools: []
  env: []
exposure:
  visible: true
  user_invocable: false
---
""", encoding="utf-8")
            (invisible_dir / "SKILL.md").write_text("""---
name: invisible-skill
description: Hidden
version: 1.0.0
requires:
  tools: []
  env: []
exposure:
  visible: false
  user_invocable: false
---
""", encoding="utf-8")

            loader = SkillLoader(user_dir=Path(td))
            catalog = SkillCatalog(loader)
            catalog.reload()

        names = {e.name for e in catalog.list_all()}
        assert "visible-skill" in names
        assert "invisible-skill" not in names

    def test_available_skills_xml_format(self):
        """get_available_skills_for_prompt() produces valid XML structure."""
        from agent_os_orchestrator.src.skills.skill_loader import SkillLoader
        from agent_os_orchestrator.src.skills.skill_catalog import SkillCatalog

        with tempfile.TemporaryDirectory() as td:
            skill_dir = Path(td) / "weather"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text("""---
name: weather
description: Get weather forecasts
version: 1.0.0
requires:
  tools: []
  env: []
exposure:
  visible: true
  user_invocable: false
---
""", encoding="utf-8")
            loader = SkillLoader(user_dir=Path(td))
            catalog = SkillCatalog(loader)
            catalog.reload()

        xml = catalog.get_available_skills_for_prompt()
        assert "<available_skills>" in xml
        assert "</available_skills>" in xml
        assert "<name>weather</name>" in xml
        assert "<description>Get weather forecasts</description>" in xml

    def test_get_skill_content_returns_markdown(self):
        """get_skill_content() returns full SKILL.md text."""
        from agent_os_orchestrator.src.skills.skill_loader import SkillLoader
        from agent_os_orchestrator.src.skills.skill_catalog import SkillCatalog

        with tempfile.TemporaryDirectory() as td:
            skill_dir = Path(td) / "memo"
            skill_dir.mkdir()
            body = """---
name: memo
description: Take notes
version: 1.0.0
requires:
  tools: []
  env: []
exposure:
  visible: true
  user_invocable: false
---
# Memo Skill
Usage: blah blah
"""
            (skill_dir / "SKILL.md").write_text(body, encoding="utf-8")
            loader = SkillLoader(user_dir=Path(td))
            catalog = SkillCatalog(loader)
            catalog.reload()

        content = catalog.get_skill_content("memo")
        assert content is not None
        assert "# Memo Skill" in content
        assert "Usage: blah blah" in content

    def test_get_skill_content_nonexistent(self):
        """get_skill_content() returns None for unknown skill."""
        from agent_os_orchestrator.src.skills.skill_catalog import SkillCatalog
        catalog = SkillCatalog()
        catalog.reload()
        assert catalog.get_skill_content("does-not-exist") is None

    def test_version_increments_on_reload(self):
        """catalog.version increments after each reload()."""
        from agent_os_orchestrator.src.skills.skill_loader import SkillLoader
        from agent_os_orchestrator.src.skills.skill_catalog import SkillCatalog

        with tempfile.TemporaryDirectory() as td:
            skill_dir = Path(td) / "s1"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text("""---
name: s1
description: desc
version: 1.0.0
requires:
  tools: []
  env: []
exposure:
  visible: true
  user_invocable: false
---
""", encoding="utf-8")
            loader = SkillLoader(user_dir=Path(td))
            catalog = SkillCatalog(loader)
            catalog.reload()
            v1 = catalog.version

            # Add another skill
            (Path(td) / "s2" / "SKILL.md").write_text("""---
name: s2
description: desc2
version: 1.0.0
requires:
  tools: []
  env: []
exposure:
  visible: true
  user_invocable: false
---
""", encoding="utf-8")
            catalog.reload()
            v2 = catalog.version

        assert v2 > v1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
