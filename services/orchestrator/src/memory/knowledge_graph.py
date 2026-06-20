"""Knowledge Graph — entity extraction, relation mapping, and graph queries.

Provides a lightweight knowledge graph backed by SQLite with recursive CTE
support for multi-hop traversal. Replaces the previous NetworkX implementation
while maintaining full backward compatibility.

Components:
- :class:`Entity` / :class:`Relation` — data classes for graph elements.
- :class:`EntityExtractor` — regex + heuristic entity/relation extraction.
- :class:`KnowledgeGraph` — SQLite-backed graph store (replaces NetworkX).
"""

from __future__ import annotations

import json
import logging
import re
import uuid

try:
    from pysqlite3 import dbapi2 as sqlite3  # type: ignore[import-untyped]
except ImportError:
    import sqlite3  # noqa: F401
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ── Data types ─────────────────────────────────────────────────────────


@dataclass
class Entity:
    """A node in the knowledge graph.

    Attributes:
        id: Unique entity identifier (UUID).
        name: Human-readable entity name.
        entity_type: Category (e.g. person, tool, concept, project).
        properties: Arbitrary key-value metadata.
        source_memory_ids: Memory items this entity was extracted from.
        created_at: ISO-8601 creation timestamp.
    """

    id: str = ""
    name: str = ""
    entity_type: str = "concept"
    properties: dict[str, Any] = field(default_factory=dict)
    source_memory_ids: list[str] = field(default_factory=list)
    created_at: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            self.id = str(uuid.uuid4())
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()


@dataclass
class Relation:
    """An edge in the knowledge graph.

    Attributes:
        id: Unique relation identifier.
        source_entity_id: ID of the subject entity.
        target_entity_id: ID of the object entity.
        relation_type: Predicate (e.g. uses, depends_on, is_a).
        weight: Confidence or strength of the relation [0, 1].
        properties: Arbitrary key-value metadata.
        source_memory_id: Memory item this relation was extracted from.
        created_at: ISO-8601 creation timestamp.
    """

    id: str = ""
    source_entity_id: str = ""
    target_entity_id: str = ""
    relation_type: str = "related_to"
    weight: float = 1.0
    properties: dict[str, Any] = field(default_factory=dict)
    source_memory_id: str = ""
    created_at: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            self.id = str(uuid.uuid4())
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()


# ── Entity Extractor ──────────────────────────────────────────────────


class EntityExtractor:
    """Extracts entities and relations from text using heuristics.

    Uses regex patterns to detect:
    - Named entities (capitalized phrases, quoted strings).
    - Technical terms (CamelCase identifiers, dot-separated paths).
    - Relations (X is Y, X uses Y, X depends on Y, etc.).
    """

    # Entity patterns
    _CAPITALIZED_PHRASE = re.compile(
        r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\b'
    )
    _QUOTED_STRING = re.compile(r'"([^"]{2,80})"')
    _TECHNICAL_TERM = re.compile(
        r'\b([A-Z][a-zA-Z0-9]*(?:\.[A-Z][a-zA-Z0-9]*)+)\b'
    )
    _CAMEL_CASE = re.compile(
        r'\b([a-z]+(?:[A-Z][a-z]+)+)\b'
    )
    # PascalCase technical terms with an internal uppercase boundary, e.g.
    # FastAPI, PostgreSQL (a lowercase run followed by another uppercase).
    _PASCAL_TECH = re.compile(
        r'\b([A-Z][a-z]+[A-Z][A-Za-z0-9]*)\b'
    )
    # ALL-CAPS prefix fused with PascalCase, e.g. SQLAlchemy, JSONParser.
    _ALLCAPS_PASCAL = re.compile(
        r'\b([A-Z]{2,}[a-z][A-Za-z0-9]*)\b'
    )
    # snake_case identifiers, e.g. pool_pre_ping, connection_pool.
    _SNAKE_CASE = re.compile(
        r'\b([a-z][a-z0-9]*(?:_[a-z0-9]+)+)\b'
    )

    # Relation patterns: (regex, predicate)
    _RELATION_PATTERNS: list[tuple[re.Pattern, str]] = [
        (re.compile(r'(\w[\w\s]{1,40}?)\s+(?:is\s+an?\s+|are\s+)(.+?)(?:\.|,|$)', re.I), "is_a"),
        (re.compile(r'(\w[\w\s]{1,40}?)\s+(?:uses?|utilizes?)\s+(.+?)(?:\.|,|$)', re.I), "uses"),
        (re.compile(r'(\w[\w\s]{1,40}?)\s+(?:depends?\s+on|requires?)\s+(.+?)(?:\.|,|$)', re.I), "depends_on"),
        (re.compile(r'(\w[\w\s]{1,40}?)\s+(?:contains?|has?)\s+(.+?)(?:\.|,|$)', re.I), "contains"),
        (re.compile(r'(\w[\w\s]{1,40}?)\s+(?:belongs?\s+to)\s+(.+?)(?:\.|,|$)', re.I), "belongs_to"),
        (re.compile(r'(\w[\w\s]{1,40}?)\s+(?:implements?)\s+(.+?)(?:\.|,|$)', re.I), "implements"),
        (re.compile(r'(\w[\w\s]{1,40}?)\s+(?:connects?\s+to|links?\s+to)\s+(.+?)(?:\.|,|$)', re.I), "connected_to"),
    ]

    def extract_entities(self, text: str, memory_id: str = "") -> list[Entity]:
        """Extract entities from text.

        Args:
            text: Source text to extract from.
            memory_id: Source memory item ID for provenance.

        Returns:
            List of extracted Entity objects (deduplicated by name).
        """
        seen_names: set[str] = set()
        entities: list[Entity] = []

        # Capitalized phrases
        for match in self._CAPITALIZED_PHRASE.finditer(text):
            name = match.group(1).strip()
            if name not in seen_names:
                seen_names.add(name)
                entities.append(Entity(
                    name=name,
                    entity_type="named_entity",
                    source_memory_ids=[memory_id] if memory_id else [],
                ))

        # Quoted strings
        for match in self._QUOTED_STRING.finditer(text):
            name = match.group(1).strip()
            if name not in seen_names and len(name) > 2:
                seen_names.add(name)
                entities.append(Entity(
                    name=name,
                    entity_type="quoted_term",
                    source_memory_ids=[memory_id] if memory_id else [],
                ))

        # Technical terms
        for match in self._TECHNICAL_TERM.finditer(text):
            name = match.group(1).strip()
            if name not in seen_names:
                seen_names.add(name)
                entities.append(Entity(
                    name=name,
                    entity_type="technical_term",
                    source_memory_ids=[memory_id] if memory_id else [],
                ))

        # CamelCase identifiers
        for match in self._CAMEL_CASE.finditer(text):
            name = match.group(1).strip()
            if name not in seen_names and len(name) > 4:
                seen_names.add(name)
                entities.append(Entity(
                    name=name,
                    entity_type="identifier",
                    source_memory_ids=[memory_id] if memory_id else [],
                ))

        # PascalCase technical terms (FastAPI, PostgreSQL, ...)
        for match in self._PASCAL_TECH.finditer(text):
            name = match.group(1).strip()
            if name not in seen_names and len(name) > 3:
                seen_names.add(name)
                entities.append(Entity(
                    name=name,
                    entity_type="technical_term",
                    source_memory_ids=[memory_id] if memory_id else [],
                ))

        # ALL-CAPS + PascalCase fused (SQLAlchemy, JSONParser, ...)
        for match in self._ALLCAPS_PASCAL.finditer(text):
            name = match.group(1).strip()
            if name not in seen_names and len(name) > 3:
                seen_names.add(name)
                entities.append(Entity(
                    name=name,
                    entity_type="technical_term",
                    source_memory_ids=[memory_id] if memory_id else [],
                ))

        # snake_case identifiers (pool_pre_ping, connection_pool, ...)
        for match in self._SNAKE_CASE.finditer(text):
            name = match.group(1).strip()
            if name not in seen_names and len(name) > 4:
                seen_names.add(name)
                entities.append(Entity(
                    name=name,
                    entity_type="identifier",
                    source_memory_ids=[memory_id] if memory_id else [],
                ))

        return entities[:20]  # Cap at 20 entities

    def extract_relations(self, text: str, memory_id: str = "") -> list[Relation]:
        """Extract subject-predicate-object relations from text.

        Args:
            text: Source text.
            memory_id: Source memory ID.

        Returns:
            List of extracted Relation objects (entities referenced by name).
        """
        relations: list[Relation] = []

        for pattern, predicate in self._RELATION_PATTERNS:
            for match in pattern.finditer(text):
                subject = match.group(1).strip()
                obj = match.group(2).strip()
                if len(subject) > 1 and len(obj) > 1:
                    relations.append(Relation(
                        source_entity_id=subject,  # name used as temp ID
                        target_entity_id=obj,
                        relation_type=predicate,
                        source_memory_id=memory_id,
                    ))

        return relations[:15]  # Cap at 15 relations


# ── Knowledge Graph Store (SQLite-backed) ─────────────────────────────


class KnowledgeGraph:
    """SQLite-backed knowledge graph for entity-relation storage.

    Replaces the previous NetworkX implementation with a persistent
    SQLite backend using recursive CTEs for multi-hop graph traversal.

    Maintains full backward compatibility with the NetworkX version.

    Args:
        db_path: Path to the SQLite database file. Parent directories
            are created automatically. Defaults to ``"data/kg.db"``.
    """

    def __init__(self, db_path: str = "data/kg.db") -> None:
        self._db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.row_factory = sqlite3.Row

        self._extractor = EntityExtractor()
        self._create_tables()

    def _create_tables(self) -> None:
        """Create database tables if they do not exist."""
        with self._conn:
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS entities (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    type TEXT,
                    properties TEXT,
                    source_memory_ids TEXT,
                    created_at TEXT,
                    updated_at TEXT
                )
            """)
            self._conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_entities_name ON entities(name)"
            )
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS relations (
                    id TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    relation_type TEXT NOT NULL,
                    properties TEXT,
                    valid_from TEXT,
                    valid_to TEXT,
                    confidence REAL DEFAULT 1.0,
                    source_memory_id TEXT DEFAULT '',
                    created_at TEXT,
                    FOREIGN KEY (source_id) REFERENCES entities(id),
                    FOREIGN KEY (target_id) REFERENCES entities(id)
                )
            """)
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_relations_source ON relations(source_id)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_relations_target ON relations(target_id)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_relations_type ON relations(relation_type)"
            )

    # ── Internal helpers ─────────────────────────────────────────────

    def _resolve_entity_id(self, name_or_id: str) -> str | None:
        """Resolve a name or ID to an entity ID."""
        # Try as direct ID first
        row = self._conn.execute(
            "SELECT id FROM entities WHERE id = ?", (name_or_id,)
        ).fetchone()
        if row:
            return row["id"]
        # Try as name (case-insensitive)
        row = self._conn.execute(
            "SELECT id FROM entities WHERE lower(name) = lower(?)", (name_or_id,)
        ).fetchone()
        if row:
            return row["id"]
        return None

    def _row_to_entity_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        """Convert a database row to an entity dict."""
        props = row["properties"]
        src_ids = row["source_memory_ids"]
        return {
            "id": row["id"],
            "name": row["name"],
            "entity_type": row["type"] or "",
            "properties": json.loads(props) if props else {},
            "source_memory_ids": json.loads(src_ids) if src_ids else [],
            "created_at": row["created_at"] or "",
        }

    # ── Entity Property Operations (controlled access) ─────────────────

    def update_entity_properties(
        self,
        entity_id: str,
        properties: dict[str, Any],
    ) -> bool:
        """Update an entity's properties dict via controlled interface.

        This is the preferred way for external components (ReuseTracker, etc.)
        to update entity properties without directly accessing _conn.

        Args:
            entity_id: The entity to update.
            properties: New properties dict (merged with existing).

        Returns:
            True if updated, False if entity not found.
        """
        now = datetime.now(timezone.utc).isoformat()
        # Read existing properties and merge
        entity = self.get_entity(entity_id)
        if entity is None:
            return False
        merged = {**entity.get("properties", {}), **properties}
        with self._conn:
            self._conn.execute(
                "UPDATE entities SET properties = ?, updated_at = ? WHERE id = ?",
                (json.dumps(merged, ensure_ascii=False), now, entity_id),
            )
        return True

    def query_entities_sorted_by_reuse_score(
        self,
        entity_type: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Query entities sorted by reuse_score stored in properties JSON.

        Uses Python-side sorting (SQLite can't sort by JSON field directly).
        For high-volume scenarios, consider adding a dedicated reuse_score REAL
        column with an index.

        Args:
            entity_type: Optional entity type filter.
            limit: Maximum results.

        Returns:
            Entities sorted by reuse_score descending.
        """
        if entity_type:
            rows = self._conn.execute(
                "SELECT * FROM entities WHERE type = ?", (entity_type,)
            ).fetchall()
        else:
            rows = self._conn.execute("SELECT * FROM entities").fetchall()

        results: list[dict[str, Any]] = []
        for row in rows:
            props = json.loads(row["properties"]) if row["properties"] else {}
            score = float(props.get("reuse_score", 0.0))
            if score > 0:
                results.append({
                    "id": row["id"],
                    "name": row["name"],
                    "entity_type": row["type"] or "",
                    "reuse_score": score,
                    "properties": props,
                })

        results.sort(key=lambda x: x["reuse_score"], reverse=True)
        return results[:limit]

    # ── Entity operations ─────────────────────────────────────────

    def add_entity(self, entity: Entity) -> str:
        """Add an entity to the graph.

        If an entity with the same name (case-insensitive) exists,
        merges properties and source_memory_ids.

        Returns:
            The entity ID.
        """
        now = datetime.now(timezone.utc).isoformat()

        # Check for existing entity by name
        existing = self._conn.execute(
            "SELECT * FROM entities WHERE lower(name) = lower(?)",
            (entity.name,),
        ).fetchone()

        if existing:
            # Merge properties and source_memory_ids
            existing_props = json.loads(existing["properties"]) if existing["properties"] else {}
            existing_src_ids = json.loads(existing["source_memory_ids"]) if existing["source_memory_ids"] else []
            existing_props.update(entity.properties)
            for mid in entity.source_memory_ids:
                if mid not in existing_src_ids:
                    existing_src_ids.append(mid)
            with self._conn:
                self._conn.execute(
                    """UPDATE entities SET properties = ?, source_memory_ids = ?, updated_at = ?
                       WHERE id = ?""",
                    (
                        json.dumps(existing_props, ensure_ascii=False),
                        json.dumps(existing_src_ids),
                        now,
                        existing["id"],
                    ),
                )
            return existing["id"]

        # Insert new entity
        with self._conn:
            self._conn.execute(
                """INSERT OR IGNORE INTO entities (id, name, type, properties, source_memory_ids, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    entity.id,
                    entity.name,
                    entity.entity_type,
                    json.dumps(entity.properties, ensure_ascii=False),
                    json.dumps(entity.source_memory_ids),
                    entity.created_at or now,
                    now,
                ),
            )
        return entity.id

    def get_entity(self, entity_id: str) -> dict[str, Any] | None:
        """Get entity data by ID."""
        row = self._conn.execute(
            "SELECT * FROM entities WHERE id = ?", (entity_id,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_entity_dict(row)

    def find_entity_by_name(self, name: str) -> dict[str, Any] | None:
        """Find an entity by its name (case-insensitive)."""
        row = self._conn.execute(
            "SELECT * FROM entities WHERE lower(name) = lower(?)", (name,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_entity_dict(row)

    def delete_entity(self, entity_id: str) -> bool:
        """Remove an entity and all its relations."""
        with self._conn:
            cursor = self._conn.execute(
                "DELETE FROM entities WHERE id = ?", (entity_id,)
            )
            self._conn.execute(
                "DELETE FROM relations WHERE source_id = ? OR target_id = ?",
                (entity_id, entity_id),
            )
        return cursor.rowcount > 0

    # ── Relation operations ───────────────────────────────────────

    def add_relation(self, relation: Relation) -> str:
        """Add a relation (edge) to the graph.

        If source or target entities don't exist by ID, they are
        looked up by name. If still not found, placeholder entities
        are created.

        Returns:
            The relation ID.
        """
        source_id = self._resolve_entity_id(relation.source_entity_id)
        target_id = self._resolve_entity_id(relation.target_entity_id)

        if source_id is None:
            source_id = self.add_entity(Entity(
                name=relation.source_entity_id,
                entity_type="auto_detected",
            ))
        if target_id is None:
            target_id = self.add_entity(Entity(
                name=relation.target_entity_id,
                entity_type="auto_detected",
            ))

        now = datetime.now(timezone.utc).isoformat()
        with self._conn:
            self._conn.execute(
                """INSERT INTO relations
                   (id, source_id, target_id, relation_type, properties,
                    valid_from, valid_to, confidence, source_memory_id, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, ?)""",
                (
                    relation.id,
                    source_id,
                    target_id,
                    relation.relation_type,
                    json.dumps(relation.properties, ensure_ascii=False),
                    now,
                    relation.weight,
                    relation.source_memory_id,
                    now,
                ),
            )
        return relation.id

    def get_relations(
        self,
        entity_id: str,
        direction: str = "both",
        relation_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """Get relations for an entity.

        Args:
            entity_id: Entity to query.
            direction: "outgoing", "incoming", or "both".
            relation_type: Optional filter by relation type.

        Returns:
            List of relation dictionaries.
        """
        results: list[dict[str, Any]] = []

        if direction in ("outgoing", "both"):
            query = "SELECT * FROM relations WHERE source_id = ? AND valid_to IS NULL"
            params: list[Any] = [entity_id]
            if relation_type:
                query += " AND relation_type = ?"
                params.append(relation_type)
            for row in self._conn.execute(query, params).fetchall():
                results.append({
                    "id": row["id"],
                    "source_entity_id": entity_id,
                    "target_entity_id": row["target_id"],
                    "relation_type": row["relation_type"],
                    "weight": row["confidence"],
                    "properties": json.loads(row["properties"]) if row["properties"] else {},
                })

        if direction in ("incoming", "both"):
            query = "SELECT * FROM relations WHERE target_id = ? AND valid_to IS NULL"
            params = [entity_id]
            if relation_type:
                query += " AND relation_type = ?"
                params.append(relation_type)
            for row in self._conn.execute(query, params).fetchall():
                results.append({
                    "id": row["id"],
                    "source_entity_id": row["source_id"],
                    "target_entity_id": entity_id,
                    "relation_type": row["relation_type"],
                    "weight": row["confidence"],
                    "properties": json.loads(row["properties"]) if row["properties"] else {},
                })

        return results

    def delete_relation(self, relation_id: str) -> bool:
        """Remove a relation by its ID."""
        with self._conn:
            cursor = self._conn.execute(
                "DELETE FROM relations WHERE id = ?", (relation_id,)
            )
        return cursor.rowcount > 0

    # ── Graph queries ─────────────────────────────────────────────

    def shortest_path(
        self, source_name: str, target_name: str,
    ) -> list[dict[str, Any]] | None:
        """Find the shortest path between two entities by name.

        Uses a recursive CTE for BFS traversal.

        Returns:
            List of entity dicts along the path, or None if no path exists.
        """
        source_id = self._resolve_entity_id(source_name)
        target_id = self._resolve_entity_id(target_name)
        if source_id is None or target_id is None:
            return None

        # BFS via recursive CTE
        rows = self._conn.execute("""
            WITH RECURSIVE bfs(parent, node, depth) AS (
                SELECT NULL, ?, 0
                UNION ALL
                SELECT bfs.node, r.target_id, bfs.depth + 1
                FROM relations r, bfs
                WHERE r.source_id = bfs.node
                  AND r.valid_to IS NULL
                  AND bfs.node != ?
                  AND bfs.depth < 10
            )
            SELECT node, depth FROM bfs WHERE node = ? LIMIT 1
        """, (source_id, target_id, target_id)).fetchall()

        if not rows:
            return None

        # Reconstruct path by backtracking
        path_ids = self._reconstruct_path(source_id, target_id)
        if path_ids is None:
            return None

        return [self.get_entity(eid) for eid in path_ids]  # type: ignore[misc]

    def _reconstruct_path(self, source_id: str, target_id: str) -> list[str] | None:
        """Reconstruct the shortest path using BFS parent tracking."""
        # BFS with parent tracking — use visited set in Python to avoid
        # "multiple recursive references" limitation in SQLite.
        # Collect all reachable nodes level by level.
        rows = self._conn.execute("""
            WITH RECURSIVE bfs(parent, node, depth) AS (
                SELECT NULL, ?, 0
                UNION ALL
                SELECT bfs.node, r.target_id, bfs.depth + 1
                FROM relations r, bfs
                WHERE r.source_id = bfs.node
                  AND r.valid_to IS NULL
                  AND bfs.depth < 10
            )
            SELECT parent, node, depth FROM bfs ORDER BY depth
        """, (source_id,)).fetchall()

        # Build parent map, keeping first (shortest) parent per node
        parent_map: dict[str, str | None] = {}
        parent_map[source_id] = None
        for row in rows:
            node = row["node"]
            if node not in parent_map:
                parent_map[node] = row["parent"]

        if target_id not in parent_map:
            return None

        # Backtrack
        path: list[str] = []
        current: str | None = target_id
        while current is not None:
            path.append(current)
            current = parent_map.get(current)
        path.reverse()
        return path

        # Build parent map
        parent_map: dict[str, str | None] = {}
        for row in rows:
            parent_map[row["node"]] = row["parent"]

        if target_id not in parent_map:
            return None

        # Backtrack
        path: list[str] = []
        current: str | None = target_id
        while current is not None:
            path.append(current)
            current = parent_map.get(current)
        path.reverse()
        return path

    def expand(
        self,
        entity_name: str,
        depth: int = 2,
        relation_type: str | None = None,
    ) -> dict[str, Any]:
        """Expand the neighbourhood around an entity.

        Uses recursive CTE for N-hop traversal.

        Args:
            entity_name: Starting entity.
            depth: Maximum traversal depth.
            relation_type: Optional relation type filter.

        Returns:
            Dict with center entity, neighbours, and edges.
        """
        entity_id = self._resolve_entity_id(entity_name)
        if entity_id is None:
            return {"center": None, "neighbours": [], "edges": []}

        center = self.get_entity(entity_id)

        # Recursive CTE traversal
        if relation_type:
            rows = self._conn.execute("""
                WITH RECURSIVE traverse(parent, node, rel_type, d) AS (
                    SELECT NULL, ?, '', 0
                    UNION ALL
                    SELECT t.node, r.target_id, r.relation_type, t.d + 1
                    FROM relations r, traverse t
                    WHERE r.source_id = t.node
                      AND r.relation_type = ?
                      AND r.valid_to IS NULL
                      AND t.d < ?
                )
                SELECT parent, node, rel_type, d FROM traverse WHERE d > 0
            """, (entity_id, relation_type, depth)).fetchall()
        else:
            rows = self._conn.execute("""
                WITH RECURSIVE traverse(parent, node, rel_type, d) AS (
                    SELECT NULL, ?, '', 0
                    UNION ALL
                    SELECT t.node, r.target_id, r.relation_type, t.d + 1
                    FROM relations r, traverse t
                    WHERE r.source_id = t.node
                      AND r.valid_to IS NULL
                      AND t.d < ?
                )
                SELECT parent, node, rel_type, d FROM traverse WHERE d > 0
            """, (entity_id, depth)).fetchall()

        visited: set[str] = set()
        neighbours: list[dict[str, Any]] = []
        edges: list[dict[str, Any]] = []

        for row in rows:
            edges.append({
                "source": row["parent"],
                "target": row["node"],
                "relation_type": row["rel_type"],
            })
            if row["node"] not in visited:
                visited.add(row["node"])
                entity = self.get_entity(row["node"])
                if entity:
                    neighbours.append(entity)

        return {"center": center, "neighbours": neighbours, "edges": edges}

    def query_neighbors(
        self, entity_id: str, rel_type: str | None = None, max_depth: int = 1,
    ) -> list[dict[str, Any]]:
        """Recursive CTE query for neighbours, supporting N-hop traversal.

        Args:
            entity_id: Starting entity ID.
            rel_type: Optional relation type filter.
            max_depth: Maximum traversal depth.
        Returns:
            List of dicts with ``entity``, ``depth``, and ``path``.
        """
        if rel_type:
            rows = self._conn.execute("""
                WITH RECURSIVE traverse(parent, node, rel_type, d) AS (
                    SELECT NULL, ?, '', 0
                    UNION ALL
                    SELECT t.node, r.target_id, r.relation_type, t.d + 1
                    FROM relations r, traverse t
                    WHERE r.source_id = t.node
                      AND r.relation_type = ?
                      AND r.valid_to IS NULL
                      AND t.d < ?
                )
                SELECT parent, node, rel_type, d FROM traverse WHERE d > 0
            """, (entity_id, rel_type, max_depth)).fetchall()
        else:
            rows = self._conn.execute("""
                WITH RECURSIVE traverse(parent, node, rel_type, d) AS (
                    SELECT NULL, ?, '', 0
                    UNION ALL
                    SELECT t.node, r.target_id, r.relation_type, t.d + 1
                    FROM relations r, traverse t
                    WHERE r.source_id = t.node
                      AND r.valid_to IS NULL
                      AND t.d < ?
                )
                SELECT parent, node, rel_type, d FROM traverse WHERE d > 0
            """, (entity_id, max_depth)).fetchall()

        results: list[dict[str, Any]] = []
        for row in rows:
            entity = self.get_entity(row["node"])
            results.append({
                "entity": entity,
                "depth": row["d"],
                "path": {"source": row["parent"], "target": row["node"], "relation_type": row["rel_type"]},
            })
        return results

    def expire_relation(self, relation_id: str) -> None:
        """Set valid_to = now() to mark a relation as expired."""
        now = datetime.now(timezone.utc).isoformat()
        with self._conn:
            self._conn.execute(
                "UPDATE relations SET valid_to = ? WHERE id = ?",
                (now, relation_id),
            )

    def search_entities(
        self,
        query: str,
        entity_type: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Search entities by name (substring match).

        Args:
            query: Search text.
            entity_type: Optional type filter.
            limit: Maximum results.

        Returns:
            List of matching entity dicts.
        """
        sql = "SELECT * FROM entities WHERE name LIKE ?"
        params: list[Any] = [f"%{query}%"]
        if entity_type:
            sql += " AND type = ?"
            params.append(entity_type)
        sql += " LIMIT ?"
        params.append(limit)

        rows = self._conn.execute(sql, params).fetchall()
        return [self._row_to_entity_dict(r) for r in rows]

    def get_entity_relations(self, entity_id: str) -> list[dict[str, Any]]:
        """Get all valid relations (valid_to IS NULL) for an entity."""
        rows = self._conn.execute(
            """SELECT * FROM relations
               WHERE (source_id = ? OR target_id = ?) AND valid_to IS NULL""",
            (entity_id, entity_id),
        ).fetchall()
        results: list[dict[str, Any]] = []
        for row in rows:
            results.append({
                "id": row["id"],
                "source_id": row["source_id"],
                "target_id": row["target_id"],
                "relation_type": row["relation_type"],
                "confidence": row["confidence"],
                "valid_from": row["valid_from"],
                "valid_to": row["valid_to"],
                "properties": json.loads(row["properties"]) if row["properties"] else {},
            })
        return results

    # ── Extraction from text ──────────────────────────────────────

    def extract_and_ingest(
        self,
        text: str,
        memory_id: str = "",
    ) -> dict[str, list[str]]:
        """Extract entities and relations from text and add to graph.

        Args:
            text: Source text to process.
            memory_id: Source memory ID for provenance.

        Returns:
            Dict with "entity_ids" and "relation_ids" created.
        """
        entities = self._extractor.extract_entities(text, memory_id)
        relations = self._extractor.extract_relations(text, memory_id)

        entity_ids: list[str] = []
        for entity in entities:
            eid = self.add_entity(entity)
            entity_ids.append(eid)

        relation_ids: list[str] = []
        for relation in relations:
            rid = self.add_relation(relation)
            relation_ids.append(rid)

        logger.info(
            "Ingested %d entities and %d relations from memory %s",
            len(entity_ids), len(relation_ids), memory_id,
        )
        return {"entity_ids": entity_ids, "relation_ids": relation_ids}

    # ── Statistics ────────────────────────────────────────────────

    def stats(self) -> dict[str, Any]:
        """Return graph statistics."""
        entity_count = self._conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
        relation_count = self._conn.execute(
            "SELECT COUNT(*) FROM relations WHERE valid_to IS NULL"
        ).fetchone()[0]

        # Entity type distribution
        entity_types: dict[str, int] = {}
        for row in self._conn.execute(
            "SELECT type, COUNT(*) as cnt FROM entities GROUP BY type"
        ).fetchall():
            entity_types[row["type"] or "unknown"] = row["cnt"]

        # Relation type distribution
        relation_types: dict[str, int] = {}
        for row in self._conn.execute(
            "SELECT relation_type, COUNT(*) as cnt FROM relations WHERE valid_to IS NULL GROUP BY relation_type"
        ).fetchall():
            relation_types[row["relation_type"] or "unknown"] = row["cnt"]

        return {
            "entity_count": entity_count,
            "relation_count": relation_count,
            "entity_types": entity_types,
            "relation_types": relation_types,
        }
