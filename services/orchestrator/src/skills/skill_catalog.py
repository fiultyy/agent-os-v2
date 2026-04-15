"""
SkillCatalog — Skill 注册表 + 查询接口

提供渐进式注入：
- Stage 1: <available_skills> XML（轻量列表，供 LLM 发现可用 skill）
- Stage 2: SKILL.md 完整内容（按需加载，避免 context overflow）
"""

import logging
from pathlib import Path
from typing import Dict, List, Optional

from .skill_loader import SkillEntry, SkillLoader

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# SkillCatalog
# ---------------------------------------------------------------------------


class SkillCatalog:
    """
    Skill 注册表 + 查询接口

    使用方式：
        catalog = SkillCatalog()
        catalog.reload()

        # Stage 1: 列出所有 visible skills（用于 system prompt 注入）
        available_xml = catalog.get_available_skills_for_prompt()

        # Stage 2: 按需加载完整内容
        content = catalog.get_skill_content("weather")
    """

    def __init__(self, loader: Optional[SkillLoader] = None):
        self._loader = loader or SkillLoader()
        self._entries: Dict[str, SkillEntry] = {}   # name -> entry
        self._version: int = 0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def reload(self) -> None:
        """重新扫描所有层级并构建注册表。"""
        self._entries.clear()
        for entry in self._loader.scan():
            self._entries[entry.name] = entry
        self._version += 1
        logger.debug("SkillCatalog reloaded, version=%d, count=%d",
                     self._version, len(self._entries))

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get(self, name: str) -> Optional[SkillEntry]:
        """按名称获取 enabled skill（不区分大小写）。"""
        entry = self._entries.get(name) or self._entries.get(name.lower())
        if entry is not None and not entry.is_enabled():
            return None
        return entry

    def list_all(self) -> List[SkillEntry]:
        """列出所有 enabled (visible) skills。"""
        return [entry for entry in self._entries.values() if entry.is_enabled()]

    @property
    def version(self) -> int:
        """当前版本号（每次 reload 递增，用于缓存失效）。"""
        return self._version

    # ------------------------------------------------------------------
    # Prompt injection
    # ------------------------------------------------------------------

    def get_available_skills_for_prompt(self) -> str:
        """
        生成 Stage 1: <available_skills> XML 块。

        格式：
            <available_skills>
              <skill>
                <name>weather</name>
                <description>Get weather forecasts via wttr.in</description>
                <location>~/.agent-os/skills/weather/SKILL.md</location>
              </skill>
            </available_skills>

        仅包含 exposure.visible=True 的 skills。
        """
        parts = ["<available_skills>"]
        for entry in self.list_all():
            # Expand ~ to actual home path for display
            location_str = str(entry.location)
            parts.append("  <skill>")
            parts.append(f"    <name>{_escape_xml(entry.name)}</name>")
            parts.append(f"    <description>{_escape_xml(entry.description)}</description>")
            parts.append(f"    <location>{_escape_xml(location_str)}</location>")
            parts.append("  </skill>")
        parts.append("</available_skills>")
        return "\n".join(parts)

    def get_skill_content(self, name: str) -> Optional[str]:
        """
        生成 Stage 2: 按需加载 SKILL.md 完整内容。

        读取 skill 的 SKILL.md 文件并返回原始 markdown 内容。
        如果 skill 不存在或文件读取失败，返回 None。
        """
        entry = self.get(name)
        if not entry:
            logger.warning("Skill not found: %s", name)
            return None

        skill_md = entry.location
        if not skill_md.is_file():
            logger.warning("SKILL.md not found: %s", skill_md)
            return None

        try:
            return skill_md.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError) as e:
            logger.warning("Failed to read SKILL.md for '%s': %s", name, e)
            return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _escape_xml(text: str) -> str:
    """Minimal XML escaping for text content."""
    return (
        text
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )
