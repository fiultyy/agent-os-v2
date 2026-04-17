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
        from skills.skill_loader import SkillLoader
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
        from skills.skill_loader import SkillLoader
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
        from skills.skill_loader import SkillLoader
        with tempfile.TemporaryDirectory() as td:
            bare_dir = Path(td) / "no-skill"
            bare_dir.mkdir()
            (bare_dir / "README.md").write_text("no skill here", encoding="utf-8")
            loader = SkillLoader(user_dir=Path(td))
            results = loader.scan()
        assert results == []

    def test_skips_dotfiles_and_skip_names(self):
        """Hidden directories and __pycache__ etc are skipped."""
        from skills.skill_loader import SkillLoader
        with tempfile.TemporaryDirectory() as td:
            for bad in [".hidden", "__pycache__", "node_modules"]:
                (Path(td) / bad / "SKILL.md").parent.mkdir()
                (Path(td) / bad / "SKILL.md").write_text(
                    self._make_skill_md("bad-skill"), encoding="utf-8",
                )
            (Path(td) / "good-skill").mkdir(parents=True, exist_ok=True)
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
        from skills.skill_loader import SkillLoader
        with tempfile.TemporaryDirectory() as td:
            # builtin has skill "my-skill"
            builtin = Path(td) / "builtin"
            builtin.mkdir()
            (builtin / "my-skill").mkdir(parents=True, exist_ok=True)
            (builtin / "my-skill" / "SKILL.md").write_text(
                self._make_skill_md("my-skill", "Builtin version"),
                encoding="utf-8",
            )
            # user has same skill "my-skill" — should win
            user = Path(td) / "user"
            user.mkdir()
            (user / "my-skill").mkdir(parents=True, exist_ok=True)
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
        from skills.skill_loader import SkillLoader
        loader = SkillLoader()
        root = Path("/safe/root")
        # File outside root should be rejected
        bad = Path("/etc/passwd")
        assert loader._validate_path(bad, root) is False

    def test_exposure_fields_parsed(self):
        """exposure.visible and exposure.user_invocable are parsed."""
        from skills.skill_loader import SkillLoader
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "secret").mkdir(parents=True, exist_ok=True)
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
        from skills.skill_loader import SkillLoader
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "tooled").mkdir(parents=True, exist_ok=True)
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
        from skills.skill_loader import SkillLoader
        from skills.skill_catalog import SkillCatalog

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
        from skills.skill_catalog import SkillCatalog
        catalog = SkillCatalog()
        catalog.reload()
        assert catalog.get("nonexistent-skill-xyz") is None

    def test_list_all_excludes_invisible(self):
        """list_all() excludes skills with exposure.visible=False."""
        from skills.skill_loader import SkillLoader
        from skills.skill_catalog import SkillCatalog

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
        from skills.skill_loader import SkillLoader
        from skills.skill_catalog import SkillCatalog

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
        from skills.skill_loader import SkillLoader
        from skills.skill_catalog import SkillCatalog

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
        from skills.skill_catalog import SkillCatalog
        catalog = SkillCatalog()
        catalog.reload()
        assert catalog.get_skill_content("does-not-exist") is None

    def test_version_increments_on_reload(self):
        """catalog.version increments after each reload()."""
        from skills.skill_loader import SkillLoader
        from skills.skill_catalog import SkillCatalog

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
            (Path(td) / "s2").mkdir(parents=True, exist_ok=True)
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


class TestSkillExecutor:
    """Tests for SkillExecutor."""

    def _make_skill_md(self, name, description="Test", version="1.0.0",
                       tools=None, env_vars=None, extra_yaml=""):
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
  visible: true
  user_invocable: false
{extra_yaml}
---
# Skill body
"""

    def test_execute_not_found_raises(self):
        """execute() raises SkillNotFoundError for unknown skill."""
        from skills.skill_catalog import SkillCatalog
        from skills.skill_executor import (
            SkillExecutor, SkillNotFoundError,
        )
        catalog = SkillCatalog()
        catalog.reload()
        executor = SkillExecutor(catalog)
        with pytest.raises(SkillNotFoundError):
            executor.execute("nonexistent-skill-xyz")

    def test_execute_loads_skill_and_calls_executor(self):
        """execute() loads SKILL.md content and calls tool_executor."""
        from skills.skill_loader import SkillLoader
        from skills.skill_catalog import SkillCatalog
        from skills.skill_executor import SkillExecutor

        results = []

        def fake_executor(name, content, ctx):
            results.append({"name": name, "content": content, "ctx": ctx})
            return "done"

        with tempfile.TemporaryDirectory() as td:
            sd = Path(td) / "weather"
            sd.mkdir()
            (sd / "SKILL.md").write_text(
                self._make_skill_md("weather", "Get weather forecasts"),
                encoding="utf-8",
            )
            loader = SkillLoader(user_dir=Path(td))
            catalog = SkillCatalog(loader)
            catalog.reload()
            executor = SkillExecutor(
                catalog,
                tool_executor=fake_executor,
                available_tools={"browser": lambda: None},
            )
            out = executor.execute("weather", {"city": "Shanghai"})

        assert out == "done"
        assert len(results) == 1
        assert results[0]["name"] == "weather"
        assert "Get weather forecasts" in results[0]["content"]
        assert results[0]["ctx"] == {"city": "Shanghai"}

    def test_mtime_cache_reloads_on_file_change(self):
        """mtime cache is invalidated when SKILL.md is modified."""
        from skills.skill_loader import SkillLoader
        from skills.skill_catalog import SkillCatalog
        from skills.skill_executor import SkillExecutor

        with tempfile.TemporaryDirectory() as td:
            sd = Path(td) / "memo"
            sd.mkdir()
            v1_content = self._make_skill_md("memo", "Version 1")
            (sd / "SKILL.md").write_text(v1_content, encoding="utf-8")

            loader = SkillLoader(user_dir=Path(td))
            catalog = SkillCatalog(loader)
            catalog.reload()
            executor = SkillExecutor(catalog)

            # First load
            c1 = executor.get_content("memo")
            assert c1 is not None
            assert "Version 1" in c1

            # Second load (cached)
            c2 = executor.get_content("memo")
            assert c1 == c2

            # Modify file (mtime changes)
            import time
            time.sleep(0.1)
            v2_content = self._make_skill_md("memo", "Version 2")
            (sd / "SKILL.md").write_text(v2_content, encoding="utf-8")

            # Cache should be invalidated and new content loaded
            c3 = executor.get_content("memo")
            assert c3 is not None
            assert "Version 2" in c3
            assert "Version 1" not in c3

    def test_validate_requires_missing_tool(self):
        """Missing required tool is reported as unsatisfied dependency."""
        from skills.skill_loader import SkillLoader
        from skills.skill_catalog import SkillCatalog
        from skills.skill_executor import (
            SkillExecutor, DependencyError,
        )

        with tempfile.TemporaryDirectory() as td:
            sd = Path(td) / "tooled"
            sd.mkdir()
            (sd / "SKILL.md").write_text(
                self._make_skill_md("tooled", "Has requirements",
                                    tools=["browser", "http_get"],
                                    env_vars=["OPENAI_API_KEY"]),
                encoding="utf-8",
            )
            loader = SkillLoader(user_dir=Path(td))
            catalog = SkillCatalog(loader)
            catalog.reload()
            # No tools provided → dependencies unsatisfied
            executor = SkillExecutor(
                catalog,
                available_tools={"browser": lambda: None},  # missing http_get
            )
            with pytest.raises(DependencyError) as exc_info:
                executor.execute("tooled")
            assert "tool:http_get" in str(exc_info.value)
            assert "env:OPENAI_API_KEY" in str(exc_info.value)

    def test_validate_requires_env_var(self):
        """Missing env var is reported as unsatisfied dependency."""
        from skills.skill_loader import SkillLoader
        from skills.skill_catalog import SkillCatalog
        from skills.skill_executor import (
            SkillExecutor, DependencyError,
        )

        with tempfile.TemporaryDirectory() as td:
            sd = Path(td) / "env-skill"
            sd.mkdir()
            (sd / "SKILL.md").write_text(
                self._make_skill_md("env-skill", "Needs env",
                                    env_vars=["NONEXISTENT_ENV_VAR_XYZ"]),
                encoding="utf-8",
            )
            loader = SkillLoader(user_dir=Path(td))
            catalog = SkillCatalog(loader)
            catalog.reload()
            executor = SkillExecutor(catalog, available_tools={})
            with pytest.raises(DependencyError) as exc_info:
                executor.execute("env-skill")
            assert "env:NONEXISTENT_ENV_VAR_XYZ" in str(exc_info.value)

    def test_invalidate_cache_clears_all(self):
        """invalidate_cache(None) clears all cached content."""
        from skills.skill_loader import SkillLoader
        from skills.skill_catalog import SkillCatalog
        from skills.skill_executor import SkillExecutor

        with tempfile.TemporaryDirectory() as td:
            sd = Path(td) / "cache-test"
            sd.mkdir()
            (sd / "SKILL.md").write_text(
                self._make_skill_md("cache-test"), encoding="utf-8",
            )
            loader = SkillLoader(user_dir=Path(td))
            catalog = SkillCatalog(loader)
            catalog.reload()
            executor = SkillExecutor(catalog)

            executor.get_content("cache-test")
            assert "cache-test" in executor._loaded_contents

            executor.invalidate_cache()
            assert executor._loaded_contents == {}
            assert executor._content_mtimes == {}

    def test_no_tool_executor_returns_content(self):
        """When no tool_executor is set, execute() returns content dict."""
        from skills.skill_loader import SkillLoader
        from skills.skill_catalog import SkillCatalog
        from skills.skill_executor import SkillExecutor

        with tempfile.TemporaryDirectory() as td:
            sd = Path(td) / "noexec"
            sd.mkdir()
            (sd / "SKILL.md").write_text(
                self._make_skill_md("noexec", "No executor test"),
                encoding="utf-8",
            )
            loader = SkillLoader(user_dir=Path(td))
            catalog = SkillCatalog(loader)
            catalog.reload()
            executor = SkillExecutor(catalog)
            result = executor.execute("noexec", {"foo": "bar"})

        assert isinstance(result, dict)
        assert result["skill"] == "noexec"
        assert result["context"] == {"foo": "bar"}
        assert "No executor test" in result["content"]


class TestSkillConfig:
    """Tests for SkillConfig."""

    def test_is_enabled_default_true(self):
        """Skill not in config is considered enabled by default."""
        import os
        from skills.skill_config import SkillConfig
        with tempfile.TemporaryDirectory() as td:
            cfg = SkillConfig(Path(td) / "skills.yaml")
            assert cfg.is_enabled("unknown-skill") is True

    def test_set_and_get_value(self):
        """set() then get() returns the written value."""
        from skills.skill_config import SkillConfig
        with tempfile.TemporaryDirectory() as td:
            cfg = SkillConfig(Path(td) / "skills.yaml")
            cfg.set("weather", "api_key", "secret123")
            assert cfg.get("weather", "api_key") == "secret123"

    def test_set_enabled(self):
        """set_enabled() controls the enabled flag."""
        from skills.skill_config import SkillConfig
        with tempfile.TemporaryDirectory() as td:
            cfg = SkillConfig(Path(td) / "skills.yaml")
            cfg.set_enabled("my-skill", False)
            assert cfg.is_enabled("my-skill") is False

            cfg.set_enabled("my-skill", True)
            assert cfg.is_enabled("my-skill") is True

    def test_get_default_value(self):
        """get() with missing key returns default."""
        from skills.skill_config import SkillConfig
        with tempfile.TemporaryDirectory() as td:
            cfg = SkillConfig(Path(td) / "skills.yaml")
            assert cfg.get("skill", "missing_key", "default_val") == "default_val"

    def test_remove_deletes_skill_config(self):
        """remove() deletes the skill section."""
        from skills.skill_config import SkillConfig
        with tempfile.TemporaryDirectory() as td:
            cfg = SkillConfig(Path(td) / "skills.yaml")
            cfg.set("to-remove", "key", "value")
            assert cfg.get("to-remove", "key") == "value"
            cfg.remove("to-remove")
            assert cfg.get("to-remove", "key") is None

    def test_schema_type_validation_accepts_valid(self):
        """Valid types pass schema validation."""
        from skills.skill_config import SkillConfig
        with tempfile.TemporaryDirectory() as td:
            cfg = SkillConfig(Path(td) / "skills.yaml")
            cfg.register_schema("typed-skill", {
                "timeout": {"type": "int"},
                "debug":   {"type": "bool"},
                "label":   {"type": "str"},
                "ratio":   {"type": "float"},
            })
            cfg.set("typed-skill", "timeout", 30)
            cfg.set("typed-skill", "debug", False)
            cfg.set("typed-skill", "label", "my-label")
            cfg.set("typed-skill", "ratio", 1.5)
            assert cfg.get("typed-skill", "timeout") == 30
            assert cfg.get("typed-skill", "debug") is False
            assert cfg.get("typed-skill", "label") == "my-label"
            assert cfg.get("typed-skill", "ratio") == 1.5

    def test_schema_type_validation_rejects_invalid(self):
        """Wrong types fail schema validation."""
        from skills.skill_config import (
            SkillConfig, SkillConfigError,
        )
        with tempfile.TemporaryDirectory() as td:
            cfg = SkillConfig(Path(td) / "skills.yaml")
            cfg.register_schema("strict-skill", {
                "count": {"type": "int"},
            })
            with pytest.raises(SkillConfigError):
                cfg.set("strict-skill", "count", "not-an-int")

    def test_schema_range_validation(self):
        """min/max constraints are enforced."""
        from skills.skill_config import (
            SkillConfig, SkillConfigError,
        )
        with tempfile.TemporaryDirectory() as td:
            cfg = SkillConfig(Path(td) / "skills.yaml")
            cfg.register_schema("range-skill", {
                "timeout": {"type": "int", "min": 1, "max": 300},
            })
            # Valid
            cfg.set("range-skill", "timeout", 60)
            assert cfg.get("range-skill", "timeout") == 60
            # Below min
            with pytest.raises(SkillConfigError):
                cfg.set("range-skill", "timeout", 0)
            # Above max
            with pytest.raises(SkillConfigError):
                cfg.set("range-skill", "timeout", 999)

    def test_schema_enum_validation(self):
        """enum constraint is enforced."""
        from skills.skill_config import (
            SkillConfig, SkillConfigError,
        )
        with tempfile.TemporaryDirectory() as td:
            cfg = SkillConfig(Path(td) / "skills.yaml")
            cfg.register_schema("enum-skill", {
                "log_level": {"type": "str", "enum": ["debug", "info", "warn"]},
            })
            cfg.set("enum-skill", "log_level", "info")
            with pytest.raises(SkillConfigError):
                cfg.set("enum-skill", "log_level", "verbose")

    def test_list_configured(self):
        """list_configured() returns all skills with config entries."""
        from skills.skill_config import SkillConfig
        with tempfile.TemporaryDirectory() as td:
            cfg = SkillConfig(Path(td) / "skills.yaml")
            cfg.set("skill-a", "key", "val")
            cfg.set("skill-b", "key", "val")
            configured = cfg.list_configured()
            assert "skill-a" in configured
            assert "skill-b" in configured

    def test_persists_to_yaml_file(self):
        """Config is written to YAML file and survives re-instantiation."""
        from skills.skill_config import SkillConfig
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "skills.yaml"

            # Write
            cfg1 = SkillConfig(path)
            cfg1.set("persist-test", "api_key", "supersecret")
            cfg1.set_enabled("persist-test", False)

            # Read back via new instance
            cfg2 = SkillConfig(path)
            assert cfg2.get("persist-test", "api_key") == "supersecret"
            assert cfg2.is_enabled("persist-test") is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

