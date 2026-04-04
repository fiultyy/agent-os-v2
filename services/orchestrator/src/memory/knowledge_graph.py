"""Knowledge Graph — entity extraction, relation mapping, and graph queries.

Provides a lightweight in-process knowledge graph backed by NetworkX.
Integrates with the memory system to automatically extract entities and
relations during the L2 Episodic → L3 Semantic migration.

Components:
- :class:`KnowledgeGraph` — NetworkX-backed graph store.
- :class:`EntityExtractor` — regex + heuristic entity/relation extraction.
- :class:`GraphQueryEngine` — SPARQL-lite traversal and shortest-path queries.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import networkx as nx

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


# ── Knowledge Graph Store ─────────────────────────────────────────────


class KnowledgeGraph:
    """NetworkX-backed knowledge graph for entity-relation storage.

    Entities are stored as nodes with ``entity_type`` and ``properties``.
    Relations are stored as edges with ``relation_type`` and ``weight``.

    Supports:
    - Add/query/delete entities and relations.
    - Shortest-path queries between entities.
    - Neighbour traversal (expand from an entity).
    - Graph statistics.
    """

    def __init__(self) -> None:
        self._graph = nx.DiGraph()
        self._entity_names: dict[str, str] = {}  # name_lower -> entity_id
        self._extractor = EntityExtractor()

    # ── Entity operations ─────────────────────────────────────────

    def add_entity(self, entity: Entity) -> str:
        """Add an entity to the graph.

        If an entity with the same name (case-insensitive) exists,
        merges properties and source_memory_ids.

        Returns:
            The entity ID.
        """
        name_key = entity.name.lower()
        existing_id = self._entity_names.get(name_key)

        if existing_id and existing_id in self._graph:
            # Merge into existing entity
            existing = self._graph.nodes[existing_id]
            existing["properties"].update(entity.properties)
            for mid in entity.source_memory_ids:
                if mid not in existing["source_memory_ids"]:
                    existing["source_memory_ids"].append(mid)
            return existing_id

        self._graph.add_node(
            entity.id,
            name=entity.name,
            entity_type=entity.entity_type,
            properties=entity.properties,
            source_memory_ids=entity.source_memory_ids,
            created_at=entity.created_at,
        )
        self._entity_names[name_key] = entity.id
        return entity.id

    def get_entity(self, entity_id: str) -> dict[str, Any] | None:
        """Get entity data by ID."""
        if entity_id not in self._graph:
            return None
        node = self._graph.nodes[entity_id]
        return {
            "id": entity_id,
            "name": node.get("name", ""),
            "entity_type": node.get("entity_type", ""),
            "properties": node.get("properties", {}),
            "source_memory_ids": node.get("source_memory_ids", []),
            "created_at": node.get("created_at", ""),
        }

    def find_entity_by_name(self, name: str) -> dict[str, Any] | None:
        """Find an entity by its name (case-insensitive)."""
        entity_id = self._entity_names.get(name.lower())
        if entity_id is None:
            return None
        return self.get_entity(entity_id)

    def delete_entity(self, entity_id: str) -> bool:
        """Remove an entity and all its edges."""
        if entity_id not in self._graph:
            return False
        node = self._graph.nodes[entity_id]
        name_key = node.get("name", "").lower()
        self._entity_names.pop(name_key, None)
        self._graph.remove_node(entity_id)
        return True

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

        self._graph.add_edge(
            source_id, target_id,
            relation_id=relation.id,
            relation_type=relation.relation_type,
            weight=relation.weight,
            properties=relation.properties,
            source_memory_id=relation.source_memory_id,
            created_at=relation.created_at,
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
        if entity_id not in self._graph:
            return []

        results: list[dict[str, Any]] = []

        if direction in ("outgoing", "both"):
            for _, target, data in self._graph.out_edges(entity_id, data=True):
                if relation_type and data.get("relation_type") != relation_type:
                    continue
                results.append({
                    "id": data.get("relation_id", ""),
                    "source_entity_id": entity_id,
                    "target_entity_id": target,
                    "relation_type": data.get("relation_type", ""),
                    "weight": data.get("weight", 1.0),
                    "properties": data.get("properties", {}),
                })

        if direction in ("incoming", "both"):
            for source, _, data in self._graph.in_edges(entity_id, data=True):
                if relation_type and data.get("relation_type") != relation_type:
                    continue
                results.append({
                    "id": data.get("relation_id", ""),
                    "source_entity_id": source,
                    "target_entity_id": entity_id,
                    "relation_type": data.get("relation_type", ""),
                    "weight": data.get("weight", 1.0),
                    "properties": data.get("properties", {}),
                })

        return results

    def delete_relation(self, relation_id: str) -> bool:
        """Remove a relation by its ID."""
        for u, v, data in list(self._graph.edges(data=True)):
            if data.get("relation_id") == relation_id:
                self._graph.remove_edge(u, v)
                return True
        return False

    # ── Graph queries ─────────────────────────────────────────────

    def shortest_path(
        self, source_name: str, target_name: str,
    ) -> list[dict[str, Any]] | None:
        """Find the shortest path between two entities by name.

        Returns:
            List of entity dicts along the path, or None if no path exists.
        """
        source_id = self._entity_names.get(source_name.lower())
        target_id = self._entity_names.get(target_name.lower())
        if source_id is None or target_id is None:
            return None

        try:
            path = nx.shortest_path(self._graph, source_id, target_id)
        except nx.NetworkXNoPath:
            return None

        return [self.get_entity(eid) for eid in path]  # type: ignore[misc]

    def expand(
        self,
        entity_name: str,
        depth: int = 2,
        relation_type: str | None = None,
    ) -> dict[str, Any]:
        """Expand the neighbourhood around an entity.

        Args:
            entity_name: Starting entity.
            depth: Maximum traversal depth.
            relation_type: Optional relation type filter.

        Returns:
            Dict with center entity, neighbours, and edges.
        """
        entity_id = self._entity_names.get(entity_name.lower())
        if entity_id is None:
            return {"center": None, "neighbours": [], "edges": []}

        center = self.get_entity(entity_id)
        visited: set[str] = {entity_id}
        neighbours: list[dict[str, Any]] = []
        edges: list[dict[str, Any]] = []
        frontier: list[str] = [entity_id]

        for _ in range(depth):
            next_frontier: list[str] = []
            for node_id in frontier:
                for _, target, data in self._graph.out_edges(node_id, data=True):
                    if relation_type and data.get("relation_type") != relation_type:
                        continue
                    if target not in visited:
                        visited.add(target)
                        neighbours.append(self.get_entity(target))
                        next_frontier.append(target)
                    edges.append({
                        "source": node_id,
                        "target": target,
                        "relation_type": data.get("relation_type", ""),
                    })
            frontier = next_frontier

        return {"center": center, "neighbours": neighbours, "edges": edges}

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
        query_lower = query.lower()
        results: list[dict[str, Any]] = []

        for node_id, data in self._graph.nodes(data=True):
            name = data.get("name", "")
            if query_lower not in name.lower():
                continue
            if entity_type and data.get("entity_type") != entity_type:
                continue
            results.append({
                "id": node_id,
                "name": name,
                "entity_type": data.get("entity_type", ""),
                "properties": data.get("properties", {}),
            })
            if len(results) >= limit:
                break

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
        return {
            "entity_count": self._graph.number_of_nodes(),
            "relation_count": self._graph.number_of_edges(),
            "entity_types": self._count_entity_types(),
            "relation_types": self._count_relation_types(),
        }

    def _count_entity_types(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for _, data in self._graph.nodes(data=True):
            et = data.get("entity_type", "unknown")
            counts[et] = counts.get(et, 0) + 1
        return counts

    def _count_relation_types(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for _, _, data in self._graph.edges(data=True):
            rt = data.get("relation_type", "unknown")
            counts[rt] = counts.get(rt, 0) + 1
        return counts

    # ── Helpers ───────────────────────────────────────────────────

    def _resolve_entity_id(self, name_or_id: str) -> str | None:
        """Resolve a name or ID to an entity ID."""
        if name_or_id in self._graph:
            return name_or_id
        return self._entity_names.get(name_or_id.lower())
