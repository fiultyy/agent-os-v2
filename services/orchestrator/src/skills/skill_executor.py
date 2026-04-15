"""
SkillExecutor — Skill 执行引擎

支持渐进式加载：
- Stage 1: catalog.get() 获取 index 元数据
- Stage 2: _load_skill_content() 按需加载 SKILL.md 全文（mtime 缓存）
- 验证 requires 依赖
- 调用 tool_executor 执行
"""

import logging
import os
import re
from typing import Any, Callable, Dict, List, Optional

from .skill_catalog import SkillCatalog
from .skill_loader import SkillEntry

logger = logging.getLogger(__name__)


class SkillExecutionError(Exception):
    """Raised when skill execution fails."""


class SkillNotFoundError(SkillExecutionError):
    """Raised when the requested skill is not found in the catalog."""


class DependencyError(SkillExecutionError):
    """Raised when skill dependencies (tools/env) are not satisfied."""


class SkillExecutor:
    """
    Skill 执行引擎，支持渐进式加载（Stage 1 index → Stage 2 SKILL.md）

    执行流程：
        1. catalog.get(skill_name) → 获取 index（Stage 1）
        2. _load_skill_content(skill_name) → 加载完整内容（Stage 2，mtime 缓存）
        3. _validate_requires(entry) → 验证依赖
        4. 解析 SKILL.md 中的使用说明和参数 schema
        5. 调用 tool_executor 执行
        6. 返回结果
    """

    # Regex to extract ```json ... ``` config_schema blocks from SKILL.md
    _CONFIG_SCHEMA_RE = re.compile(
        r"```json\s*\n\s*config_schema\s*:\s*\n(.*?)```",
        re.DOTALL,
    )

    # Regex to extract a simple parameters section
    _PARAMS_RE = re.compile(
        r"(?:##?\s*Parameters|##?\s*参数)\s*\n(.*?)(?=\n##?\s|\Z)",
        re.DOTALL,
    )

    def __init__(
        self,
        catalog: SkillCatalog,
        tool_executor: Any = None,
        available_tools: Optional[Dict[str, Callable]] = None,
    ):
        """
        Args:
            catalog: SkillCatalog instance for skill lookup.
            tool_executor: Optional callable or object used to execute skills.
                           If callable, called as ``tool_executor(skill_name, content, context)``.
                           If object, must have an ``execute(skill_name, content, context)`` method.
            available_tools: Optional dict of tool_name → callable for dependency validation.
        """
        self._catalog = catalog
        self._tool_executor = tool_executor
        self._available_tools = available_tools or {}
        self._loaded_contents: Dict[str, str] = {}   # name → SKILL.md content
        self._content_mtimes: Dict[str, float] = {}   # name → file mtime

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def execute(self, skill_name: str, context: Optional[Dict[str, Any]] = None) -> Any:
        """
        Execute a skill by name.

        Args:
            skill_name: Name of the skill to execute.
            context: Optional execution context dict passed to the tool executor.

        Returns:
            Whatever the tool_executor returns.

        Raises:
            SkillNotFoundError: Skill not in catalog.
            DependencyError: Required tools/env not available.
            SkillExecutionError: Execution failed.
        """
        context = context or {}

        # Stage 1: get index from catalog
        entry = self._catalog.get(skill_name)
        if entry is None:
            raise SkillNotFoundError(f"Skill not found: {skill_name!r}")

        # Stage 2: load full SKILL.md content (mtime cached)
        content = self._load_skill_content(skill_name)
        if content is None:
            raise SkillExecutionError(
                f"Failed to load SKILL.md content for: {skill_name!r}"
            )

        # Validate dependencies
        missing = self._validate_requires(entry)
        if missing:
            raise DependencyError(
                f"Skill {skill_name!r} has unsatisfied dependencies: {missing}"
            )

        # Execute via tool_executor
        result = self._dispatch(skill_name, entry, content, context)
        return result

    def get_content(self, skill_name: str) -> Optional[str]:
        """
        Get cached SKILL.md content for a skill (loads on first access).

        This is useful for agents that need to read skill instructions
        without executing the skill.
        """
        return self._load_skill_content(skill_name)

    def invalidate_cache(self, skill_name: Optional[str] = None) -> None:
        """
        Invalidate content cache.

        Args:
            skill_name: If given, invalidate only that skill. Otherwise clear all.
        """
        if skill_name is None:
            self._loaded_contents.clear()
            self._content_mtimes.clear()
        else:
            self._loaded_contents.pop(skill_name, None)
            self._content_mtimes.pop(skill_name, None)

    # ------------------------------------------------------------------
    # Stage 2: Content loading with mtime cache
    # ------------------------------------------------------------------

    def _load_skill_content(self, name: str) -> Optional[str]:
        """
        Stage 2: Load SKILL.md on demand with mtime-based cache invalidation.

        Returns:
            SKILL.md content string, or None if skill not found or file unreadable.
        """
        entry = self._catalog.get(name)
        if not entry:
            return None

        skill_md_path = entry.location

        # Get current mtime
        try:
            current_mtime = skill_md_path.stat().st_mtime
        except OSError:
            logger.warning("Cannot stat %s for skill %r", skill_md_path, name)
            return None

        cached_mtime = self._content_mtimes.get(name)

        # Reload if mtime changed or no cache
        if current_mtime != cached_mtime:
            try:
                content = skill_md_path.read_text(encoding="utf-8")
                self._loaded_contents[name] = content
                self._content_mtimes[name] = current_mtime
                logger.debug("Loaded SKILL.md for %r (%d bytes)", name, len(content))
            except (UnicodeDecodeError, OSError) as e:
                logger.warning("Failed to read %s: %s", skill_md_path, e)
                return None

        return self._loaded_contents.get(name)

    # ------------------------------------------------------------------
    # Dependency validation
    # ------------------------------------------------------------------

    def _validate_requires(self, entry: SkillEntry) -> List[str]:
        """
        Validate that required tools and env vars are available.

        Returns:
            List of missing dependency descriptions. Empty if all satisfied.
        """
        missing: List[str] = []

        # Check tools
        for tool_name in entry.requires.tools:
            if tool_name not in self._available_tools:
                missing.append(f"tool:{tool_name}")

        # Check environment variables
        for env_var in entry.requires.env:
            if not os.environ.get(env_var):
                missing.append(f"env:{env_var}")

        return missing

    # ------------------------------------------------------------------
    # Execution dispatch
    # ------------------------------------------------------------------

    def _dispatch(
        self,
        skill_name: str,
        entry: SkillEntry,
        content: str,
        context: Dict[str, Any],
    ) -> Any:
        """Dispatch skill execution to the configured tool_executor."""
        if self._tool_executor is None:
            # No executor configured — return parsed content for the caller
            logger.info("No tool_executor configured, returning skill content for %r", skill_name)
            return {
                "skill": skill_name,
                "content": content,
                "entry": entry,
                "context": context,
            }

        # Callable executor
        if callable(self._tool_executor):
            return self._tool_executor(skill_name, content, context)

        # Object with execute() method
        if hasattr(self._tool_executor, "execute"):
            return self._tool_executor.execute(skill_name, content, context)  # type: ignore[union-attr]

        raise SkillExecutionError(
            f"Invalid tool_executor type: {type(self._tool_executor)}"
        )
