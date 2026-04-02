"""Prompt version control."""

from typing import Any


class PromptVersion:
    """Tracks versions of prompt templates."""

    def __init__(self, prompt_id: str):
        self.prompt_id = prompt_id
        self.versions: list[dict[str, Any]] = []

    def create_version(self, content: str, metadata: dict[str, Any] | None = None) -> int:
        """Create a new version, return version number."""
        version = len(self.versions) + 1
        self.versions.append({"version": version, "content": content, "metadata": metadata})
        return version

    def get_version(self, version: int) -> dict[str, Any] | None:
        """Get a specific version."""
        for v in self.versions:
            if v["version"] == version:
                return v
        return None

    def get_latest(self) -> dict[str, Any] | None:
        """Get the latest version."""
        return self.versions[-1] if self.versions else None
