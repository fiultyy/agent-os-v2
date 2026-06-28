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
from pathlib import Path
import json
import logging

from .profile import AgentBaseProfile, LayerProfile

logger = logging.getLogger(__name__)


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

        Parses standard workspace files and maps them to layers:
        - SOUL.md → L0 (Model Identity)
        - AGENTS.md → L1 (Agent Identity) + L2 (Operational Guidelines)

        If a file does not exist, the corresponding layers are skipped silently.

        Args:
            agent_id: The agent identifier for the new profile.
            workspace_path: Path to the workspace directory containing the files.

        Returns:
            A new AgentBaseProfile with layers populated from files.
        """
        workspace = Path(workspace_path)
        profile = AgentBaseProfile(agent_id=agent_id)

        # SOUL.md → L0
        soul_file = workspace / "SOUL.md"
        if soul_file.is_file():
            content = soul_file.read_text(encoding="utf-8").strip()
            if content:
                profile.add_layer(LayerProfile(
                    layer=0,
                    source="soul_md",
                    content=content,
                    priority=100,
                    tags=["identity", "soul"],
                ))
                logger.debug("Loaded SOUL.md for agent %s (%d chars)", agent_id, len(content))
        else:
            logger.debug("SOUL.md not found at %s, skipping L0", soul_file)

        # AGENTS.md → L1 (identity) + L2 (guidelines)
        agents_file = workspace / "AGENTS.md"
        if agents_file.is_file():
            content = agents_file.read_text(encoding="utf-8").strip()
            if content:
                # Split on major section headers to distribute content
                sections = self._split_agents_md_sections(content)

                # Skip empty initial sections (content starts with a header = no preamble)
                non_empty = [s for s in sections if s.strip()]
                if not non_empty:
                    return profile

                # First non-empty section → L1 (Agent Identity)
                profile.add_layer(LayerProfile(
                    layer=1,
                    source="agents_md_identity",
                    content=non_empty[0].strip(),
                    priority=90,
                    tags=["identity", "agents"],
                ))

                # Remaining non-empty sections → L2 (Operational Guidelines)
                if len(non_empty) > 1:
                    remaining = "\n\n".join(non_empty[1:]).strip()
                    if remaining:
                        profile.add_layer(LayerProfile(
                            layer=2,
                            source="agents_md_guidelines",
                            content=remaining,
                            priority=80,
                            tags=["guidelines", "agents"],
                        ))

                logger.debug("Loaded AGENTS.md for agent %s (%d chars, %d sections)",
                             agent_id, len(content), len(sections))
        else:
            logger.debug("AGENTS.md not found at %s, skipping L1/L2", agents_file)

        return profile

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
