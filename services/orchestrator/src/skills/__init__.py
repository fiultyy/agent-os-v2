"""
Agent OS User Skill System — P0 + P1 + P2 + P3
"""

from .skill_loader import SkillLoader, SkillEntry, SkillRequires, SkillExposure
from .skill_catalog import SkillCatalog
from .skill_executor import SkillExecutor, SkillExecutionError, SkillNotFoundError, DependencyError
from .skill_config import SkillConfig, SkillConfigError
from .tool_registry_integration import SkillToolRegistryBridge, ToolRegistryLike
from .prompt_integration import (
    build_skills_prompt,
    build_skills_prompt_from_entries,
    format_skill_entry,
    AVAILABLE_SKILLS_TEMPLATE,
    SKILL_ENTRY_TEMPLATE,
)
from .hot_reload import SkillHotReloader

__all__ = [
    # P0: Loader + Catalog
    "SkillLoader",
    "SkillEntry",
    "SkillRequires",
    "SkillExposure",
    "SkillCatalog",
    # P1: Executor + Config
    "SkillExecutor",
    "SkillExecutionError",
    "SkillNotFoundError",
    "DependencyError",
    "SkillConfig",
    "SkillConfigError",
    # P2-A: ToolRegistry Integration
    "SkillToolRegistryBridge",
    "ToolRegistryLike",
    # P2-B: Prompt Integration
    "build_skills_prompt",
    "build_skills_prompt_from_entries",
    "format_skill_entry",
    "AVAILABLE_SKILLS_TEMPLATE",
    "SKILL_ENTRY_TEMPLATE",
    # P3-A: Hot Reload
    "SkillHotReloader",
]
