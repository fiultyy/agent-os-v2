"""
Prompt Integration — Stage 1 <available_skills> XML generation

生成 <available_skills> XML 块并注入 system prompt，
供 LLM 在回复前扫描并按需加载 SKILL.md。

格式：
    <available_skills>
      <skill>
        <name>weather</name>
        <description>Get weather forecasts via wttr.in</description>
        <location>~/.agent-os/skills/weather/SKILL.md</location>
      </skill>
    </available_skills>

    ## Skills (mandatory)
    Before replying: scan <available_skills> <description> entries.
    - If exactly one skill clearly applies: read its SKILL.md at <location> with `read`, then follow it.
    - If multiple could apply: choose the most specific one, then read/follow it.
    - If none clearly apply: do not read any SKILL.md.
    Constraints: never read more than one skill up front; only read after selecting.
"""

import logging
from typing import List

from .skill_catalog import SkillCatalog
from .skill_loader import SkillEntry

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

AVAILABLE_SKILLS_TEMPLATE = """<available_skills>
{skills_entries}
</available_skills>

## Skills (mandatory)
Before replying: scan <available_skills> <description> entries.
- If exactly one skill clearly applies: read its SKILL.md at <location> with `read`, then follow it.
- If multiple could apply: choose the most specific one, then read/follow it.
- If none clearly apply: do not read any SKILL.md.
Constraints: never read more than one skill up front; only read after selecting."""

SKILL_ENTRY_TEMPLATE = """  <skill>
    <name>{name}</name>
    <description>{description}</description>
    <location>{location}</location>
  </skill>"""


# ---------------------------------------------------------------------------
# XML helpers
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


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_skills_prompt(catalog: SkillCatalog) -> str:
    """
    生成 Stage 1: <available_skills> XML 块。

    从 catalog 中获取所有 visible skills 并渲染为 XML 格式，
    附带 mandatory scan 指令，供 LLM 在回复前扫描。

    Args:
        catalog: SkillCatalog instance.

    Returns:
        Formatted XML string ready for system prompt injection.
    """
    entries = catalog.list_all()
    entries_xml = "\n".join(
        _format_skill_entry(e) for e in entries
    )
    return AVAILABLE_SKILLS_TEMPLATE.format(skills_entries=entries_xml)


def build_skills_prompt_from_entries(entries: List[SkillEntry]) -> str:
    """
    从 SkillEntry 列表直接生成 <available_skills> XML 块。

    适用于不需要访问完整 SkillCatalog 的场景（如测试）。

    Args:
        entries: List of SkillEntry objects.

    Returns:
        Formatted XML string.
    """
    entries_xml = "\n".join(
        _format_skill_entry(e) for e in entries
    )
    return AVAILABLE_SKILLS_TEMPLATE.format(skills_entries=entries_xml)


def _format_skill_entry(entry: SkillEntry) -> str:
    """Format a single SkillEntry as XML."""
    location_str = str(entry.location)
    return SKILL_ENTRY_TEMPLATE.format(
        name=_escape_xml(entry.name),
        description=_escape_xml(entry.description),
        location=_escape_xml(location_str),
    )


def format_skill_entry(entry: SkillEntry) -> str:
    """
    Format a single SkillEntry as an XML <skill> block.

    Exposed for testing and external use.
    """
    return _format_skill_entry(entry)
