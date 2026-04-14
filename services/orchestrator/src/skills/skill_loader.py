"""
SkillLoader — 三层优先级目录扫描 + YAML frontmatter 解析

扫描三层目录，按优先级高覆盖低：
- user:     ~/.agent-os/skills/       (priority 200)
- project:  <cwd>/.agent-os/skills/  (priority 100)
- builtin:  <pkg>/skills/            (priority 0)
"""

import logging
import re
import yaml
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class SkillExposure:
    visible: bool = True
    user_invocable: bool = False


@dataclass
class SkillRequires:
    tools: List[str] = field(default_factory=list)
    env: List[str] = field(default_factory=list)


@dataclass
class SkillEntry:
    name: str
    description: str
    version: str
    location: Path          # SKILL.md path
    base_dir: Path           # skill root directory
    source: str              # "user" | "project" | "builtin"
    requires: SkillRequires = field(default_factory=SkillRequires)
    exposure: SkillExposure = field(default_factory=SkillExposure)


# ---------------------------------------------------------------------------
# Frontmatter parser
# ---------------------------------------------------------------------------

# Matches YAML frontmatter block: --- ... ---
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _parse_frontmatter(content: str) -> Dict[str, Any]:
    """Parse YAML frontmatter from SKILL.md content using PyYAML."""
    match = _FRONTMATTER_RE.match(content)
    if not match:
        return {}
    raw = match.group(1).strip()
    try:
        parsed = yaml.safe_load(raw)
        return dict(parsed) if parsed else {}
    except yaml.YAMLError as e:
        logger.warning("YAML parse error in frontmatter: %s", e)
        return {}


# ---------------------------------------------------------------------------
# SkillLoader
# ---------------------------------------------------------------------------

_MAX_SKILL_MD_SIZE = 64 * 1024   # 64 KB


class SkillLoader:
    """
    三层优先级目录扫描 + YAML frontmatter 解析
    """

    PRIORITY: Dict[str, int] = {
        "user":    200,
        "project": 100,
        "builtin":   0,
    }

    # Directories to skip in scanning
    SKIP_NAMES: Set[str] = frozenset({
        "__pycache__", "node_modules", ".git", ".svn", ".hg",
    })

    def __init__(
        self,
        user_dir: Optional[Path] = None,
        project_dir: Optional[Path] = None,
        builtin_dir: Optional[Path] = None,
    ):
        self._user_dir    = user_dir    or (Path.home() / ".agent-os" / "skills")
        self._project_dir = project_dir or (Path.cwd()  / ".agent-os" / "skills")
        self._builtin_dir = builtin_dir or self._infer_builtin_dir()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def scan(self) -> List[SkillEntry]:
        """
        扫描所有层级，返回去重后的 skill 列表（高优先级覆盖低优先级）。
        """
        collected: Dict[str, SkillEntry] = {}   # name -> entry

        for source, dir_path in [
            ("builtin", self._builtin_dir),
            ("project", self._project_dir),
            ("user",    self._user_dir),
        ]:
            if not dir_path or not dir_path.is_dir():
                continue
            for entry in self._scan_dir(dir_path, source):
                # Higher priority source wins (user > project > builtin)
                if entry.name not in collected:
                    collected[entry.name] = entry

        return list(collected.values())

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _scan_dir(self, dir_path: Path, source: str) -> List[SkillEntry]:
        """扫描单个目录，返回所有有效 SkillEntry。"""
        results: List[SkillEntry] = []

        if not dir_path.is_dir():
            return results

        for skill_dir in dir_path.iterdir():
            if not skill_dir.is_dir():
                continue
            if skill_dir.name.startswith("."):
                continue
            if skill_dir.name in self.SKIP_NAMES:
                continue

            skill_md = skill_dir / "SKILL.md"
            if not skill_md.is_file():
                continue

            entry = self._parse_skill_md(skill_md, source)
            if entry:
                results.append(entry)

        return results

    def _parse_skill_md(self, path: Path, source: str) -> Optional[SkillEntry]:
        """解析单个 SKILL.md 文件，提取 frontmatter。"""
        # Size guard
        try:
            size = path.stat().st_size
            if size > _MAX_SKILL_MD_SIZE:
                logger.warning("SKILL.md too large (%d KB): %s", size // 1024, path)
                return None
        except OSError:
            return None

        # Path safety check
        if not self._validate_path(path, path.parent):
            return None

        try:
            content = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError) as e:
            logger.warning("Failed to read %s: %s", path, e)
            return None

        fm = _parse_frontmatter(content)

        if not fm.get("name"):
            logger.warning("SKILL.md missing 'name' field: %s", path)
            return None

        return SkillEntry(
            name=fm.get("name", path.parent.name),
            description=str(fm.get("description", "")),
            version=str(fm.get("version", "1.0.0")),
            location=path,
            base_dir=path.parent,
            source=source,
            requires=SkillRequires(
                tools=list(fm.get("requires", {}).get("tools", []))
                       if isinstance(fm.get("requires"), dict)
                       else [],
                env=list(fm.get("requires", {}).get("env", []))
                    if isinstance(fm.get("requires"), dict)
                    else [],
            ),
            exposure=SkillExposure(
                visible=bool(fm.get("exposure", {}).get("visible", True))
                        if isinstance(fm.get("exposure"), dict)
                        else True,
                user_invocable=bool(fm.get("exposure", {}).get("user_invocable", False))
                               if isinstance(fm.get("exposure"), dict)
                               else False,
            ),
        )

    def _validate_path(self, skill_path: Path, root: Path) -> bool:
        """
        安全检查：防止路径逃逸。
        realpath(skill_path) 必须在 realpath(root) 之内。
        """
        try:
            resolved_file = skill_path.resolve()
            resolved_root = root.resolve()
            # Ensure the file is actually inside root
            resolved_root_len = len(resolved_root.parts)
            return (
                resolved_file.parts[:resolved_root_len] == resolved_root.parts
                and resolved_file != resolved_root
            )
        except OSError:
            return False

    def _infer_builtin_dir(self) -> Path:
        """推断 builtin skills 目录：<pkg>/skills/"""
        # __file__ is .../agent-os/services/orchestrator/src/skills/skill_loader.py
        return Path(__file__).parent.parent.parent.parent / "skills"
