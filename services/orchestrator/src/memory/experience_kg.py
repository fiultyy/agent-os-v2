"""ExperienceKG — workspace-level experience knowledge graph.

Independent from the full transcripts KG. Organizes experience nodes
using butterfly bidirectional associations (forward/backward wings).
Provides skill bundle interface for workspace-shared experiences.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from pysqlite3 import dbapi2 as sqlite3  # type: ignore[import-untyped]
except ImportError:
    import sqlite3  # noqa: F401


@dataclass
class ExperienceNode:
    """A single experience node in the experience KG."""

    id: str = ""
    content: str = ""
    skill_domain: str = ""
    reuse_score: float = 0.0
    source_entity_id: str = ""
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            self.id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        if not self.created_at:
            self.created_at = now
        if not self.updated_at:
            self.updated_at = now


@dataclass
class ExperienceWing:
    """A butterfly wing edge between experience nodes."""

    id: str = ""
    source_node_id: str = ""
    target_node_id: str = ""
    wing_type: str = "forward"  # forward | backward | bidirectional
    strength: float = 1.0
    confidence: float = 0.5
    created_at: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            self.id = str(uuid.uuid4())
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()


@dataclass
class SkillBundle:
    """A bundle of experience nodes forming a reusable skill."""

    id: str = ""
    name: str = ""
    domain: str = ""
    node_ids: list[str] = field(default_factory=list)
    description: str = ""
    created_at: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            self.id = str(uuid.uuid4())
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()


class ExperienceKG:
    """Workspace-level experience knowledge graph.

    Independent SQLite tables (not shared with transcripts KG):
    - experience_nodes
    - experience_wings
    - skill_bundles
    """

    def __init__(self, db_path: str = "data/experience_kg.db") -> None:
        self._db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.row_factory = sqlite3.Row
        self._create_tables()

    def _create_tables(self) -> None:
        with self._conn:
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS experience_nodes (
                    id TEXT PRIMARY KEY,
                    content TEXT NOT NULL,
                    skill_domain TEXT DEFAULT '',
                    reuse_score REAL DEFAULT 0.0,
                    source_entity_id TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            self._conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_exp_nodes_domain
                ON experience_nodes(skill_domain)
            """)
            self._conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_exp_nodes_score
                ON experience_nodes(reuse_score DESC)
            """)
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS experience_wings (
                    id TEXT PRIMARY KEY,
                    source_node_id TEXT NOT NULL,
                    target_node_id TEXT NOT NULL,
                    wing_type TEXT NOT NULL,
                    strength REAL DEFAULT 1.0,
                    confidence REAL DEFAULT 0.5,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (source_node_id) REFERENCES experience_nodes(id),
                    FOREIGN KEY (target_node_id) REFERENCES experience_nodes(id)
                )
            """)
            self._conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_exp_wings_source
                ON experience_wings(source_node_id)
            """)
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS skill_bundles (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    domain TEXT DEFAULT '',
                    node_ids TEXT NOT NULL,
                    description TEXT DEFAULT '',
                    created_at TEXT NOT NULL
                )
            """)
            self._conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_skill_bundles_domain
                ON skill_bundles(domain)
            """)
            self._conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_exp_wings_target
                ON experience_wings(target_node_id)
            """)

    # ── Experience Nodes ─────────────────────────────────────────

    def create_experience_node(
        self,
        content: str,
        skill_domain: str,
        source_entity_id: str = "",
        reuse_score: float = 0.0,
    ) -> str:
        """Create an experience node from a high-reuse entity."""
        node = ExperienceNode(
            content=content,
            skill_domain=skill_domain,
            source_entity_id=source_entity_id,
            reuse_score=reuse_score,
        )
        with self._conn:
            self._conn.execute(
                """INSERT INTO experience_nodes
                   (id, content, skill_domain, reuse_score, source_entity_id, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    node.id,
                    node.content,
                    node.skill_domain,
                    node.reuse_score,
                    node.source_entity_id,
                    node.created_at,
                    node.updated_at,
                ),
            )
        return node.id

    def get_experience_node(self, node_id: str) -> dict[str, Any] | None:
        """Get an experience node by ID."""
        row = self._conn.execute(
            "SELECT * FROM experience_nodes WHERE id = ?", (node_id,)
        ).fetchone()
        if row is None:
            return None
        return dict(row)

    def get_top_experience_nodes(
        self, domain: str | None = None, limit: int = 20
    ) -> list[dict[str, Any]]:
        """Get experience nodes sorted by reuse_score descending."""
        if domain:
            rows = self._conn.execute(
                """SELECT * FROM experience_nodes
                   WHERE skill_domain = ?
                   ORDER BY reuse_score DESC
                   LIMIT ?""",
                (domain, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                """SELECT * FROM experience_nodes
                   ORDER BY reuse_score DESC
                   LIMIT ?""",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def update_reuse_score(self, node_id: str, score_delta: float) -> bool:
        """Increment the reuse_score of an experience node."""
        now = datetime.now(timezone.utc).isoformat()
        cursor = self._conn.execute(
            """UPDATE experience_nodes
               SET reuse_score = reuse_score + ?, updated_at = ?
               WHERE id = ?""",
            (score_delta, now, node_id),
        )
        return cursor.rowcount > 0

    # ── Experience Wings ──────────────────────────────────────────

    def create_wing_edge(
        self,
        source_node_id: str,
        target_node_id: str,
        wing_type: str,
        strength: float = 1.0,
        confidence: float = 0.5,
    ) -> str:
        """Create a butterfly wing edge between two experience nodes."""
        wing = ExperienceWing(
            source_node_id=source_node_id,
            target_node_id=target_node_id,
            wing_type=wing_type,
            strength=strength,
            confidence=confidence,
        )
        with self._conn:
            self._conn.execute(
                """INSERT INTO experience_wings
                   (id, source_node_id, target_node_id, wing_type, strength, confidence, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    wing.id,
                    wing.source_node_id,
                    wing.target_node_id,
                    wing.wing_type,
                    wing.strength,
                    wing.confidence,
                    wing.created_at,
                ),
            )
        return wing.id

    def get_forward_wings(self, node_id: str) -> list[dict[str, Any]]:
        """Get forward wings (outgoing) from a node."""
        rows = self._conn.execute(
            """SELECT * FROM experience_wings
               WHERE source_node_id = ? AND wing_type IN ('forward', 'bidirectional')
               ORDER BY strength DESC""",
            (node_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_backward_wings(self, node_id: str) -> list[dict[str, Any]]:
        """Get backward wings (incoming) to a node."""
        rows = self._conn.execute(
            """SELECT * FROM experience_wings
               WHERE target_node_id = ? AND wing_type IN ('backward', 'bidirectional')
               ORDER BY strength DESC""",
            (node_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    # ── Butterfly Associations ────────────────────────────────────

    def build_butterfly_associations(
        self, trigger_node_id: str
    ) -> dict[str, Any]:
        """Build butterfly bidirectional associations for a trigger node.

        Returns:
            Dict with forward_nodes, backward_nodes, and composite_score.
        """
        trigger = self.get_experience_node(trigger_node_id)
        if not trigger:
            return {"forward": [], "backward": [], "composite_score": 0.0}

        forward_rows = self._conn.execute(
            """SELECT n.*, w.strength as wing_strength, w.confidence as wing_confidence
               FROM experience_wings w
               JOIN experience_nodes n ON n.id = w.target_node_id
               WHERE w.source_node_id = ?
                 AND w.wing_type IN ('forward', 'bidirectional')
               ORDER BY w.strength DESC
               LIMIT 10""",
            (trigger_node_id,),
        ).fetchall()

        backward_rows = self._conn.execute(
            """SELECT n.*, w.strength as wing_strength, w.confidence as wing_confidence
               FROM experience_wings w
               JOIN experience_nodes n ON n.id = w.source_node_id
               WHERE w.target_node_id = ?
                 AND w.wing_type IN ('backward', 'bidirectional')
               ORDER BY w.strength DESC
               LIMIT 10""",
            (trigger_node_id,),
        ).fetchall()

        forward_nodes = [
            {**dict(row), "wing_strength": row["wing_strength"], "wing_confidence": row["wing_confidence"]}
            for row in forward_rows
        ]
        backward_nodes = [
            {**dict(row), "wing_strength": row["wing_strength"], "wing_confidence": row["wing_confidence"]}
            for row in backward_rows
        ]

        # Composite score: average of forward and backward strengths
        fwd_avg = sum(n["wing_strength"] for n in forward_nodes) / max(len(forward_nodes), 1)
        bwd_avg = sum(n["wing_strength"] for n in backward_nodes) / max(len(backward_nodes), 1)
        composite = (fwd_avg + bwd_avg) / 2

        return {
            "center": trigger,
            "forward": forward_nodes,
            "backward": backward_nodes,
            "composite_score": composite,
        }

    # ── Skill Bundles ────────────────────────────────────────────

    def create_skill_bundle(
        self,
        name: str,
        node_ids: list[str],
        domain: str,
        description: str = "",
    ) -> str:
        """Create a skill bundle from multiple experience nodes."""
        bundle = SkillBundle(
            name=name,
            domain=domain,
            node_ids=node_ids,
            description=description,
        )
        with self._conn:
            self._conn.execute(
                """INSERT INTO skill_bundles
                   (id, name, domain, node_ids, description, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    bundle.id,
                    bundle.name,
                    bundle.domain,
                    json.dumps(bundle.node_ids),
                    bundle.description,
                    bundle.created_at,
                ),
            )
        return bundle.id

    def get_skill_bundle(self, bundle_id: str) -> dict[str, Any] | None:
        """Get a skill bundle by ID."""
        row = self._conn.execute(
            "SELECT * FROM skill_bundles WHERE id = ?", (bundle_id,)
        ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["node_ids"] = json.loads(result["node_ids"])
        return result

    def get_skill_bundles(self, domain: str | None = None) -> list[dict[str, Any]]:
        """Get all skill bundles, optionally filtered by domain."""
        if domain:
            rows = self._conn.execute(
                "SELECT * FROM skill_bundles WHERE domain = ? ORDER BY created_at DESC",
                (domain,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM skill_bundles ORDER BY created_at DESC"
            ).fetchall()
        results = []
        for row in rows:
            result = dict(row)
            result["node_ids"] = json.loads(result["node_ids"])
            results.append(result)
        return results

    def get_skill_bundle_nodes(self, bundle_id: str) -> list[dict[str, Any]]:
        """Get all experience nodes in a skill bundle."""
        bundle = self.get_skill_bundle(bundle_id)
        if not bundle:
            return []
        nodes = []
        for node_id in bundle["node_ids"]:
            node = self.get_experience_node(node_id)
            if node:
                nodes.append(node)
        return nodes

    # ── Statistics ──────────────────────────────────────────────

    def stats(self) -> dict[str, Any]:
        """Return experience KG statistics."""
        node_count = self._conn.execute(
            "SELECT COUNT(*) FROM experience_nodes"
        ).fetchone()[0]
        wing_count = self._conn.execute(
            "SELECT COUNT(*) FROM experience_wings"
        ).fetchone()[0]
        bundle_count = self._conn.execute(
            "SELECT COUNT(*) FROM skill_bundles"
        ).fetchone()[0]

        # Domain distribution
        domains: dict[str, int] = {}
        for row in self._conn.execute(
            "SELECT skill_domain, COUNT(*) as cnt FROM experience_nodes GROUP BY skill_domain"
        ).fetchall():
            domains[row["skill_domain"] or "unknown"] = row["cnt"]

        return {
            "node_count": node_count,
            "wing_count": wing_count,
            "bundle_count": bundle_count,
            "domains": domains,
        }
