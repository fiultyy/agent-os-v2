"""
SkillConfig — Skill 配置管理 + schema 验证

管理 skills.yaml 配置文件，提供：
- enabled/disabled 开关
- per-skill 自定义配置项
- schema 验证（基于 SKILL.md 中声明的 config_schema）
"""

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

logger = logging.getLogger(__name__)


class SkillConfigError(Exception):
    """Raised on config validation errors."""


class SkillConfig:
    """
    Skill 配置管理 + schema 验证

    配置文件格式（YAML）::

        skills:
          <skill-name>:
            enabled: true
            <custom-key>: <custom-value>

    用法::

        config = SkillConfig(Path("skills.yaml"))
        config.is_enabled("weather")           # True
        config.get("weather", "api_key")       # "xxx"
        config.set("weather", "api_key", "yyy")  # writes + validates
    """

    def __init__(self, config_path: Path):
        """
        Args:
            config_path: Path to the skills.yaml config file.
                         Created on first write if it doesn't exist.
        """
        self._config_path = config_path
        self._config: Dict[str, Any] = self._load()
        # Schema cache: skill_name → dict of {key: {type, ...}}
        self._schemas: Dict[str, Dict[str, Dict[str, Any]]] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_enabled(self, skill_name: str) -> bool:
        """Check whether a skill is enabled (defaults to True if not configured)."""
        skill_cfg = self._get_skill_section(skill_name)
        if skill_cfg is None:
            return True  # unlisted skills are enabled by default
        return bool(skill_cfg.get("enabled", True))

    def get(self, skill_name: str, key: str, default: Any = None) -> Any:
        """Get a config value for a skill."""
        skill_cfg = self._get_skill_section(skill_name)
        if skill_cfg is None:
            return default
        return skill_cfg.get(key, default)

    def set(self, skill_name: str, key: str, value: Any) -> None:
        """
        Set a config value for a skill (with schema validation if available).

        Raises:
            SkillConfigError: If validation against registered schema fails.
        """
        # Validate against registered schema
        if not self._validate_schema(skill_name, key, value):
            raise SkillConfigError(
                f"Schema validation failed for skill={skill_name!r}, "
                f"key={key!r}, value={value!r}"
            )

        # Ensure the skill section exists
        skills = self._config.setdefault("skills", {})
        skill_cfg = skills.setdefault(skill_name, {})
        skill_cfg[key] = value

        self._save()

    def set_enabled(self, skill_name: str, enabled: bool) -> None:
        """Convenience: enable or disable a skill."""
        self.set(skill_name, "enabled", enabled)

    def list_configured(self) -> List[str]:
        """List all skill names that have config entries."""
        skills = self._config.get("skills", {})
        return list(skills.keys())

    def remove(self, skill_name: str) -> None:
        """Remove all config for a skill."""
        skills = self._config.get("skills", {})
        if skill_name in skills:
            del skills[skill_name]
            self._save()

    # ------------------------------------------------------------------
    # Schema registration
    # ------------------------------------------------------------------

    def register_schema(
        self, skill_name: str, schema: Dict[str, Dict[str, Any]]
    ) -> None:
        """
        Register a config schema for a skill.

        Schema format::

            {
                "api_key": {"type": "string", "required": True},
                "timeout": {"type": "int", "min": 1, "max": 300},
                "debug":   {"type": "bool"},
            }
        """
        self._schemas[skill_name] = schema

    def get_schema(self, skill_name: str) -> Dict[str, Dict[str, Any]]:
        """Get the registered schema for a skill (empty dict if none)."""
        return self._schemas.get(skill_name, {})

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _get_skill_section(self, skill_name: str) -> Optional[Dict[str, Any]]:
        """Get the config dict for a skill, or None."""
        skills = self._config.get("skills", {})
        cfg = skills.get(skill_name)
        if cfg is None or not isinstance(cfg, dict):
            return None
        return cfg

    def _validate_schema(self, skill_name: str, key: str, value: Any) -> bool:
        """
        Validate a config value against the registered schema.

        Returns True if:
          - No schema registered for this skill, OR
          - No schema for this key, OR
          - Value passes validation
        """
        schema = self._schemas.get(skill_name)
        if schema is None:
            return True  # no schema → anything goes

        key_schema = schema.get(key)
        if key_schema is None:
            return True  # no constraint for this key

        expected_type = key_schema.get("type")
        if expected_type:
            if not self._check_type(value, expected_type):
                logger.warning(
                    "Type mismatch for %s.%s: expected %s, got %s",
                    skill_name, key, expected_type, type(value).__name__,
                )
                return False

        # Numeric range checks
        if isinstance(value, (int, float)):
            min_val = key_schema.get("min")
            max_val = key_schema.get("max")
            if min_val is not None and value < min_val:
                logger.warning(
                    "Value %s for %s.%s below min %s", value, skill_name, key, min_val,
                )
                return False
            if max_val is not None and value > max_val:
                logger.warning(
                    "Value %s for %s.%s above max %s", value, skill_name, key, max_val,
                )
                return False

        # Enum check
        allowed = key_schema.get("enum")
        if allowed is not None and value not in allowed:
            logger.warning(
                "Value %r for %s.%s not in allowed values %r",
                value, skill_name, key, allowed,
            )
            return False

        return True

    @staticmethod
    def _check_type(value: Any, expected: str) -> bool:
        """Check if value matches the expected type string."""
        type_map = {
            "str": str,
            "string": str,
            "int": int,
            "float": float,
            "bool": bool,
            "list": list,
            "dict": dict,
        }
        # Allow int where float is expected
        if expected in ("float",) and isinstance(value, int) and not isinstance(value, bool):
            return True
        py_type = type_map.get(expected)
        if py_type is None:
            return True  # unknown type spec → allow
        # bool is subclass of int; check bool first
        if expected in ("bool",) and isinstance(value, bool):
            return True
        if expected in ("int",) and isinstance(value, bool):
            return False
        return isinstance(value, py_type)

    def _load(self) -> Dict[str, Any]:
        """Load config from YAML file. Returns empty dict if file missing."""
        if not self._config_path.is_file():
            return {}
        try:
            raw = self._config_path.read_text(encoding="utf-8")
            data = yaml.safe_load(raw)
            return dict(data) if data else {}
        except (yaml.YAMLError, OSError) as e:
            logger.warning("Failed to load skill config %s: %s", self._config_path, e)
            return {}

    def _save(self) -> None:
        """Persist config to YAML file."""
        try:
            # Ensure parent directory exists
            self._config_path.parent.mkdir(parents=True, exist_ok=True)
            yaml_str = yaml.dump(self._config, default_flow_style=False, allow_unicode=True)
            self._config_path.write_text(yaml_str, encoding="utf-8")
        except OSError as e:
            logger.error("Failed to save skill config to %s: %s", self._config_path, e)
            raise
