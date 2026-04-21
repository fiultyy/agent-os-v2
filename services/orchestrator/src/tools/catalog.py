"""Tool Catalog (L3.4) — unified directory indexing tools by category/tag/version.

Provides a searchable, filterable catalog that indexes tools across all three
tool layers (L3.1 Primitive, L3.2 Skill, L3.3 Composite).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


class ToolLayer(Enum):
    """Tool hierarchy layers."""

    PRIMITIVE = "L3.1"
    SKILL = "L3.2"
    COMPOSITE = "L3.3"


@dataclass
class ToolCatalogEntry:
    """A single entry in the tool catalog."""

    name: str
    layer: ToolLayer
    category: str
    tags: list[str] = field(default_factory=list)
    version: str = "1.0.0"
    description: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    handler_ref: str = ""  # opaque reference to the actual handler

    def matches_tags(self, tags: list[str]) -> bool:
        """Return True if this entry has any of the given tags."""
        return any(t in self.tags for t in tags)


@dataclass
class ToolCatalog:
    """Unified tool catalog — indexes tools by layer, category, and tags."""

    entries: dict[str, ToolCatalogEntry] = field(default_factory=dict)

    # -- mutators -----------------------------------------------------------

    def register(self, entry: ToolCatalogEntry) -> None:
        """Register (or overwrite) a catalog entry."""
        self.entries[entry.name] = entry

    def unregister(self, name: str) -> Optional[ToolCatalogEntry]:
        """Remove an entry and return it, or None."""
        return self.entries.pop(name, None)

    # -- queries ------------------------------------------------------------

    def find(self, name: str) -> Optional[ToolCatalogEntry]:
        """Look up a single entry by exact name."""
        return self.entries.get(name)

    def list_all(self) -> list[ToolCatalogEntry]:
        """Return all catalog entries."""
        return list(self.entries.values())

    def list_by_layer(self, layer: ToolLayer) -> list[ToolCatalogEntry]:
        """Return entries matching a specific tool layer."""
        return [e for e in self.entries.values() if e.layer == layer]

    def list_by_category(self, category: str) -> list[ToolCatalogEntry]:
        """Return entries whose category matches exactly."""
        return [e for e in self.entries.values() if e.category == category]

    def list_by_tags(self, tags: list[str]) -> list[ToolCatalogEntry]:
        """Return entries that match *any* of the given tags."""
        return [e for e in self.entries.values() if e.matches_tags(tags)]

    def list_versions(self, name: str) -> list[str]:
        """Return the version(s) known for a tool (currently single-version)."""
        entry = self.entries.get(name)
        return [entry.version] if entry else []

    def search(self, query: str) -> list[ToolCatalogEntry]:
        """Case-insensitive fuzzy search across name, description, and category."""
        q = query.lower()
        return [
            e
            for e in self.entries.values()
            if q in e.name.lower()
            or q in e.description.lower()
            or q in e.category.lower()
        ]

    def count(self) -> int:
        """Total number of registered entries."""
        return len(self.entries)


class ToolCatalogAPI:
    """Thin serialization helper for HTTP API responses."""

    def __init__(self, catalog: ToolCatalog) -> None:
        self.catalog = catalog

    # -- serialization ------------------------------------------------------

    @staticmethod
    def _entry_to_dict(entry: ToolCatalogEntry, *, include_description: bool = True) -> dict:
        d: dict = {
            "name": entry.name,
            "layer": entry.layer.value,
            "category": entry.category,
            "tags": entry.tags,
            "version": entry.version,
        }
        if include_description:
            d["description"] = entry.description
        return d

    def to_dict(self) -> dict:
        """Serialize the full catalog."""
        return {
            "tools": [self._entry_to_dict(e) for e in self.catalog.list_all()],
            "total": self.catalog.count(),
        }

    def filter_dict(
        self,
        layer: str | None = None,
        category: str | None = None,
        tags: list[str] | None = None,
    ) -> dict:
        """Serialize filtered catalog entries."""
        entries = self.catalog.list_all()

        if layer is not None:
            layer_enum = ToolLayer(layer)
            entries = [e for e in entries if e.layer == layer_enum]

        if category is not None:
            entries = [e for e in entries if e.category == category]

        if tags:
            entries = [e for e in entries if e.matches_tags(tags)]

        return {
            "tools": [self._entry_to_dict(e, include_description=False) for e in entries],
            "total": len(entries),
        }
