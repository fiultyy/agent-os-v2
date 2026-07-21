"""ProfileRegistry - pluggable profile registration and file-based loader.

Supports:
- In-memory registration of AgentBaseProfile instances
- Loading profiles from workspace files (SOUL.md → L0, AGENTS.md → L1+L2)
- Profile switching at runtime

NOT-WIRED (deferred): profile 系统零生产接线 —— ProfileRegistry 仅被
test_profile.py 实例化,list_profiles/load_from_files/save_to_file/load_from_file
等 API 仅 test 引用。architect.py/taskspec.py 有 profile_registry 引用但未实例化。
保留作为 defer 子系统待接线决策。
"""
import json
import logging
import os
from pathlib import Path

from .profile import AgentBaseProfile, LayerProfile

logger = logging.getLogger(__name__)

# ADR(harness-adr.md L26)用户指令位置:固定文件名 + 固定顺序 + per-file 截断(抄 claw
# CONTEXT_FILE_ORDER)。identity/user 是 claw 特定身份/用户偏好,AO2 无对应语义 → 不引入
# (YAGNI,决策点 2A)。AGENTS.md 单独 _split 拆 L1(identity)+ L2(guidelines),不进此列表。
_STABLE_FILES: list[tuple[str, int, str, int, list[str]]] = [
    ("SOUL.md", 0, "soul_md", 100, ["identity", "soul"]),
    ("MEMORY.md", 3, "memory_md", 70, ["memory"]),
    ("TOOLS.md", 4, "tools_md", 60, ["tools"]),
    ("BOOTSTRAP.md", 4, "bootstrap_md", 55, ["bootstrap"]),
]
# per-file 最大长度裁剪(保前段核心,防爆 context 稀释纪律段;env 旋钮默认 8192)。
_MAX_CHARS = int(os.getenv("AO2_PROFILE_FILE_MAX_CHARS", "8192"))


class ProfileRegistry:
    """Manages registration, storage, and loading of AgentBaseProfile instances.

    Thread-safety note: This class is not thread-safe for concurrent modifications.
    Wrap with external locking if needed in multi-threaded contexts.
    """

    def __init__(self):
        self._profiles: dict[str, AgentBaseProfile] = {}

    def register(self, profile: AgentBaseProfile) -> str:
        """Register an AgentBaseProfile.

        Args:
            profile: The profile to register.

        Returns:
            The agent_id of the registered profile.

        Raises:
            ValueError: If a profile with the same agent_id is already registered.
        """
        agent_id = profile.agent_id
        if agent_id in self._profiles:
            logger.warning("Overwriting existing profile for agent_id=%s", agent_id)
        self._profiles[agent_id] = profile.copy()
        return agent_id

    def get(self, agent_id: str) -> AgentBaseProfile | None:
        """Retrieve a registered profile by agent_id.

        Returns a copy to prevent accidental mutation of registry state.

        Args:
            agent_id: The agent identifier.

        Returns:
            A copy of the registered profile, or None if not found.
        """
        profile = self._profiles.get(agent_id)
        return profile.copy() if profile else None

    def list_profiles(self) -> list[str]:
        """List all registered agent_ids.

        Returns:
            Sorted list of agent_id strings.
        """
        return sorted(self._profiles.keys())

    def unregister(self, agent_id: str) -> bool:
        """Remove a profile from the registry.

        Args:
            agent_id: The agent identifier.

        Returns:
            True if removed, False if not found.
        """
        if agent_id in self._profiles:
            del self._profiles[agent_id]
            return True
        return False

    def load_from_files(self, agent_id: str, workspace_path: str) -> AgentBaseProfile:
        """Load a profile from workspace files.

        Parses standard workspace files and maps them to layers(ADR L26 固定文件名 +
        固定顺序 + per-file 截断,抄 claw CONTEXT_FILE_ORDER):
        - AGENTS.md → L1 (Agent Identity) + L2 (Operational Guidelines),_split by header
        - SOUL.md → L0, MEMORY.md → L3, TOOLS.md/BOOTSTRAP.md → L4

        每文件 read 后按 ``AO2_PROFILE_FILE_MAX_CHARS``(默认 8192)截断保前段核心。
        文件不存在静默跳过。

        Args:
            agent_id: The agent identifier for the new profile.
            workspace_path: Path to the workspace directory containing the files.

        Returns:
            A new AgentBaseProfile with layers populated from files.
        """
        workspace = Path(workspace_path)
        profile = AgentBaseProfile(agent_id=agent_id)

        # AGENTS.md → L1 (identity) + L2 (guidelines),_split(特殊:双段)
        agents_file = workspace / "AGENTS.md"
        if agents_file.is_file():
            content = self._read_truncated(agents_file)
            if content:
                self._add_agents_layers(profile, agent_id, content)
        else:
            logger.debug("AGENTS.md not found at %s, skipping L1/L2", agents_file)

        # 其余 stable 文件:per-file 截断 + add_layer(ADR L26)
        for basename, layer, source, priority, tags in _STABLE_FILES:
            f = workspace / basename
            if not f.is_file():
                logger.debug("%s not found at %s, skipping L%d", basename, f, layer)
                continue
            content = self._read_truncated(f)
            if content:
                profile.add_layer(LayerProfile(
                    layer=layer, source=source, content=content,
                    priority=priority, tags=tags,
                ))
                logger.debug("Loaded %s for agent %s (%d chars, L%d)",
                             basename, agent_id, len(content), layer)

        return profile

    def _read_truncated(self, path: Path) -> str:
        """read + strip + per-file 截断保前段(ADR L26),超额追加 [truncated] 标记。"""
        content = path.read_text(encoding="utf-8").strip()
        if len(content) > _MAX_CHARS:
            content = content[:_MAX_CHARS] + f"\n\n...[truncated {len(content) - _MAX_CHARS} chars]"
        return content

    def _add_agents_layers(self, profile: AgentBaseProfile, agent_id: str, content: str) -> None:
        """AGENTS.md _split → L1 (identity) + L2 (guidelines)。"""
        sections = self._split_agents_md_sections(content)
        non_empty = [s for s in sections if s.strip()]
        if not non_empty:
            return
        profile.add_layer(LayerProfile(
            layer=1, source="agents_md_identity",
            content=non_empty[0].strip(), priority=90, tags=["identity", "agents"],
        ))
        if len(non_empty) > 1:
            remaining = "\n\n".join(non_empty[1:]).strip()
            if remaining:
                profile.add_layer(LayerProfile(
                    layer=2, source="agents_md_guidelines",
                    content=remaining, priority=80, tags=["guidelines", "agents"],
                ))
        logger.debug("Loaded AGENTS.md for agent %s (%d chars, %d sections)",
                     agent_id, len(content), len(sections))

    def _split_agents_md_sections(self, content: str) -> list[str]:
        """Split AGENTS.md content into logical sections.

        Uses markdown headers (# ## ### ####) as primary delimiters.
        Falls back to blank-line-separated paragraphs.

        Preserves header markers in the section text for downstream use.

        Returns:
            List of section strings (empty strings filtered out).
        """
        import re

        header_pattern = re.compile(r'^#{1,4} .*$', re.MULTILINE)
        matches = list(header_pattern.finditer(content))

        if not matches:
            # No headers - split by double newlines
            return [p.strip() for p in content.split("\n\n") if p.strip()]

        result: list[str] = []
        for i, m in enumerate(matches):
            header = m.group()
            start = m.end()
            # Content runs from end of this header to start of next header (or end of string)
            end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
            section_content = content[start:end].strip()
            result.append(f"{header}\n{section_content}")

        return [s for s in result if s.strip()]

    def save_to_file(self, agent_id: str, path: Path) -> None:
        """Serialize a profile to a JSON file.

        Args:
            agent_id: The agent identifier.
            path: Destination file path.
        """
        profile = self.get(agent_id)
        if profile is None:
            raise ValueError(f"No profile found for agent_id={agent_id}")
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(profile.to_dict(), f, indent=2, ensure_ascii=False)

    def load_from_file(self, path: Path) -> AgentBaseProfile:
        """Load a profile from a JSON file.

        Args:
            path: Source file path.

        Returns:
            The loaded AgentBaseProfile.

        Raises:
            FileNotFoundError: If the file does not exist.
            ValueError: If the JSON is invalid or missing required fields.
        """
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return AgentBaseProfile.from_dict(data)
