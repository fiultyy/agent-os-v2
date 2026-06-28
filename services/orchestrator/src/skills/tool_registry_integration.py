"""
ToolRegistry Integration — Bridge between SkillCatalog and L3 ToolRegistry

将 SkillCatalog 中注册的 skills 自动注册到 L3 ToolRegistry，
使 skills 可以通过统一的 tool call 机制被调用。

注册格式：
    tool name:  skill:<skill-name>
    handler:    SkillExecutor.execute（绑定 skill_name）
    description: 从 SkillEntry.description
    parameters: 从 SKILL.md frontmatter parameters schema
"""

import logging
from typing import Any, Callable, Dict, List, Optional

from .skill_catalog import SkillCatalog
from .skill_loader import SkillEntry

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tool metadata schema
# ---------------------------------------------------------------------------

# Minimal tool parameters schema inferred from SkillEntry.
# Real parameters should come from SKILL.md frontmatter `parameters` field.
DEFAULT_PARAMETERS_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "context": {
            "type": "object",
            "description": "Execution context passed to the skill handler.",
        }
    },
    "required": [],
}


def _extract_parameters_schema(entry: SkillEntry) -> Dict[str, Any]:
    """
    Extract tool parameters schema from SkillEntry.

    Currently reads from the skill's base_dir / config_schema.json if present,
    or falls back to DEFAULT_PARAMETERS_SCHEMA.
    Future: parse `parameters` field from SKILL.md frontmatter.
    """
    schema_file = entry.base_dir / "config_schema.json"
    if schema_file.is_file():
        try:
            import json
            return json.loads(schema_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to load config_schema.json for %s: %s", entry.name, e)
    return DEFAULT_PARAMETERS_SCHEMA.copy()


# ---------------------------------------------------------------------------
# ToolRegistry interface (L3 integration point)
# ---------------------------------------------------------------------------

class ToolRegistryLike:
    """
    Abstract interface that L3 ToolRegistry must implement.

    This defines the minimal contract required by SkillToolRegistryBridge.
    Replace with the actual L3 type when integrating.
    """

    def register(
        self,
        name: str,
        handler: Callable[..., Any],
        description: str = "",
        parameters: Dict[str, Any] | None = None,
    ) -> None:
        """Register a tool."""
        ...

    def unregister(self, name: str) -> None:
        """Unregister a tool by name."""
        ...

    def get(self, name: str) -> Dict[str, Any] | None:
        """Get tool metadata by name."""
        ...

    def list_tools(self) -> List[Dict[str, Any]]:
        """List all registered tools."""
        ...


# ---------------------------------------------------------------------------
# SkillToolRegistryBridge
# ---------------------------------------------------------------------------

class SkillToolRegistryBridge:
    """
    Skill 系统与 L3 ToolRegistry 的桥接。

    将 SkillCatalog 中所有 visible skills 注册到 ToolRegistry，
    使其可以通过统一的 `skill:<name>` tool call 机制被 LLM 调用。

    使用方式：
        catalog = SkillCatalog()
        catalog.reload()

        bridge = SkillToolRegistryBridge(catalog, tool_registry)
        bridge.register_all()

        # 当 catalog reload 时同步 registry
        catalog.reload()
        bridge.sync()

    注册格式：
        - tool name:  skill:<skill-name>
        - handler:    bound SkillExecutor.execute method
        - description: SkillEntry.description
        - parameters:  inferred from SKILL.md or config_schema.json
    """

    # Prefix used to namespace skill tools in the registry
    TOOL_PREFIX = "skill:"

    def __init__(
        self,
        catalog: SkillCatalog,
        registry: ToolRegistryLike,
        skill_executor: Optional[Any] = None,
    ):
        """
        Args:
            catalog: SkillCatalog instance to bridge from.
            registry: ToolRegistry-like instance to bridge to.
            skill_executor: Optional SkillExecutor for actual execution.
                           If not provided, uses a lazy executor that calls
                           catalog.get() + get_skill_content().
        """
        self._catalog = catalog
        self._registry = registry
        self._executor = skill_executor
        # Track registered skill names for sync
        self._registered_names: Dict[str, str] = {}  # tool_name → skill_name

    # ------------------------------------------------------------------
    # Core registration
    # ------------------------------------------------------------------

    def register_all(self) -> None:
        """
        将 catalog 中所有 visible skills 注册到 registry。

        跳过已注册且未被 catalog 移除的 skills（增量注册）。
        """
        visible = self._catalog.list_all()
        for entry in visible:
            tool_name = self._tool_name(entry.name)
            if tool_name not in self._registered_names:
                self._register_entry(entry)

        logger.debug("Registered %d skills to ToolRegistry", len(self._registered_names))

    def register_skill(self, name: str) -> bool:
        """
        注册单个 skill 到 registry。

        Args:
            name: Skill name (case-insensitive).

        Returns:
            True if registered, False if skill not found.
        """
        entry = self._catalog.get(name)
        if not entry:
            logger.warning("Cannot register skill %r: not found in catalog", name)
            return False

        if not entry.exposure.visible:
            logger.warning("Cannot register skill %r: exposure.visible is False", name)
            return False

        tool_name = self._tool_name(entry.name)
        self._register_entry(entry)
        return True

    def unregister_skill(self, name: str) -> bool:
        """
        从 registry 移除 skill。

        Args:
            name: Skill name.

        Returns:
            True if unregistered, False if was not registered.
        """
        tool_name = self._tool_name(name)
        if tool_name not in self._registered_names:
            return False

        try:
            self._registry.unregister(tool_name)
        except Exception as e:
            logger.warning("Failed to unregister tool %s: %s", tool_name, e)

        self._registered_names.pop(tool_name, None)
        logger.debug("Unregistered skill:%s", name)
        return True

    def sync(self) -> None:
        """
        同步：重新扫描 catalog 并更新 registry。

        - 移除 catalog 中已删除的 skills
        - 添加 catalog 中新增的 visible skills
        - 跳过未变化的 skills
        """
        current_visible = {e.name for e in self._catalog.list_all()}

        # Remove skills no longer in catalog
        for tool_name, skill_name in list(self._registered_names.items()):
            if skill_name not in current_visible:
                self.unregister_skill(skill_name)

        # Add new skills
        for entry in self._catalog.list_all():
            tool_name = self._tool_name(entry.name)
            if tool_name not in self._registered_names:
                self._register_entry(entry)

        logger.debug("Sync complete: %d skills registered", len(self._registered_names))

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def registered_count(self) -> int:
        """Number of skills currently registered in the registry."""
        return len(self._registered_names)

    def is_registered(self, skill_name: str) -> bool:
        """Check if a skill is currently registered."""
        return self._tool_name(skill_name) in self._registered_names

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _tool_name(self, skill_name: str) -> str:
        """Build the tool name for a skill."""
        return f"{self.TOOL_PREFIX}{skill_name}"

    def _build_handler(self, skill_name: str) -> Callable[..., Any]:
        """
        Build a handler function that executes the skill.

        Uses the bound SkillExecutor if provided,
        otherwise falls back to a simple content-returning callable.
        """

        def handler(context: Optional[Dict[str, Any]] = None) -> Any:
            if self._executor is not None:
                return self._executor.execute(skill_name, context=context or {})

            # Fallback: return skill content without execution
            content = self._catalog.get_skill_content(skill_name)
            entry = self._catalog.get(skill_name)
            return {
                "skill": skill_name,
                "content": content,
                "entry": entry,
                "context": context or {},
            }

        # Name the handler for better debugging/tracing
        handler.__name__ = f"skill_handler_{skill_name}"
        return handler

    def _register_entry(self, entry: SkillEntry) -> None:
        """Register a single SkillEntry to the registry."""
        tool_name = self._tool_name(entry.name)
        handler = self._build_handler(entry.name)
        parameters = _extract_parameters_schema(entry)

        self._registry.register(
            name=tool_name,
            handler=handler,
            description=entry.description,
            parameters=parameters,
        )
        self._registered_names[tool_name] = entry.name
        logger.debug("Registered %s → %s", entry.name, tool_name)
