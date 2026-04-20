"""Butterfly Wing — bidirectional associative memory activation mechanism.

The butterfly model implements two wings of associative recall:
- **Forward Wing (Inductive)**: generalization, diffusion, aggregation
  (input → generalization). Derived from temporal/entity/thematic context.
- **Backward Wing (Anchor)**: anchoring, verification, contraction
  (generalization → concrete). Derived from citations and usage context.
- **Composite Score**: bidirectional coordination of both wings.

18 Feature Checklist:
  F1-F6: Forward wing (metadata/score/threshold/trigger/output/cache)
  B1-B6: Backward wing (metadata/score/threshold/trigger/output/cache)
  C1-C6: Coordination (lifecycle/persistence/debug/API/perf constraints)
"""

from __future__ import annotations

import json
import math
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from enum import Enum
from pathlib import Path
from typing import Literal, Optional

try:
    from pysqlite3 import dbapi2 as sqlite3  # type: ignore[import-untyped]
except ImportError:
    import sqlite3  # noqa: F401


# ─────────────────────────────────────────────────────────────────
# WingType Enum
# ─────────────────────────────────────────────────────────────────

class WingType(Enum):
    """Wing classification for the butterfly model."""

    FORWARD = "forward"          # Forward wing (inductive/generalization)
    BACKWARD = "backward"        # Backward wing (anchor/verification)
    BIDIRECTIONAL = "bidirectional"  # Both wings active


# ─────────────────────────────────────────────────────────────────
# WingMetadata — F1 / B1: Wing metadata
# ─────────────────────────────────────────────────────────────────

@dataclass
class WingMetadata:
    """Metadata for a single wing of the butterfly model.

    F1 (forward metadata) and B1 (backward metadata) share this structure.
    """

    wing: WingType
    strength: float = 1.0           # F1/B1: Associative strength 0-1
    confidence: float = 0.5         # F1/B1: Confidence level 0-1
    expires_at: Optional[datetime] = None  # F1/B1: Expiry timestamp
    trigger_type: str = ""          # F4/B4: Trigger type (temporal/entity/citation)
    association_desc: str = ""     # F1/B1: Human-readable association description

    def is_expired(self) -> bool:
        """Check if this wing metadata has expired."""
        if self.expires_at is None:
            return False
        return datetime.now(timezone.utc) > self.expires_at


# ─────────────────────────────────────────────────────────────────
# ButterflyWing — Full butterfly structure
# ─────────────────────────────────────────────────────────────────

@dataclass
class ButterflyWing:
    """Complete butterfly wing structure containing both forward and backward wings.

    F2/F3/F5: Forward wing score, threshold, output associations
    B2/B3/B5: Backward wing score, threshold, output associations
    C1/C2:    Composite score and activation status
    """

    # Forward wing (inductive/generalization)
    forward_metadata: Optional[WingMetadata] = None
    forward_score: float = 0.0          # F2: Association score 0-1
    forward_associations: list[str] = field(default_factory=list)  # F5: Output format

    # Backward wing (anchor/verification)
    backward_metadata: Optional[WingMetadata] = None
    backward_score: float = 0.0          # B2: Association score 0-1
    backward_associations: list[str] = field(default_factory=list)  # B5: Output format

    # Bidirectional coordination
    composite_score: float = 0.0        # C1: Weighted composite score
    is_active: bool = False              # C2: Whether wing is activated

    @classmethod
    def create_empty(cls) -> "ButterflyWing":
        """Create an empty butterfly wing with default values."""
        return cls(
            forward_metadata=WingMetadata(wing=WingType.FORWARD),
            backward_metadata=WingMetadata(wing=WingType.BACKWARD),
        )

    def to_dict(self) -> dict:
        """Serialize to a plain dict for storage."""
        def _meta_to_dict(m: WingMetadata | None) -> dict | None:
            if m is None:
                return None
            return {
                "wing": m.wing.value,
                "strength": m.strength,
                "confidence": m.confidence,
                "expires_at": m.expires_at.isoformat() if m.expires_at else None,
                "trigger_type": m.trigger_type,
                "association_desc": m.association_desc,
            }

        return {
            "forward_metadata": _meta_to_dict(self.forward_metadata),
            "forward_score": self.forward_score,
            "forward_associations": self.forward_associations,
            "backward_metadata": _meta_to_dict(self.backward_metadata),
            "backward_score": self.backward_score,
            "backward_associations": self.backward_associations,
            "composite_score": self.composite_score,
            "is_active": self.is_active,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ButterflyWing":
        """Deserialize from a plain dict."""
        def _dict_to_meta(m: dict | None) -> WingMetadata | None:
            if m is None:
                return None
            expires = None
            if m.get("expires_at"):
                expires = datetime.fromisoformat(m["expires_at"])
            return WingMetadata(
                wing=WingType(m["wing"]),
                strength=m.get("strength", 1.0),
                confidence=m.get("confidence", 0.5),
                expires_at=expires,
                trigger_type=m.get("trigger_type", ""),
                association_desc=m.get("association_desc", ""),
            )

        return cls(
            forward_metadata=_dict_to_meta(d.get("forward_metadata")),
            forward_score=d.get("forward_score", 0.0),
            forward_associations=d.get("forward_associations", []),
            backward_metadata=_dict_to_meta(d.get("backward_metadata")),
            backward_score=d.get("backward_score", 0.0),
            backward_associations=d.get("backward_associations", []),
            composite_score=d.get("composite_score", 0.0),
            is_active=d.get("is_active", False),
        )


# ─────────────────────────────────────────────────────────────────
# ButterflyEngine — F2-F6 / B2-B6 / C1-C2: Computation engine
# ─────────────────────────────────────────────────────────────────

class ButterflyEngine:
    """Engine for computing butterfly wing scores and metadata.

    Implements the forward (inductive) and backward (anchor) wing
    computation algorithms, plus composite score coordination.
    """

    # F3 / B3: Activation thresholds
    DEFAULT_FORWARD_THRESHOLD: float = 0.5
    DEFAULT_BACKWARD_THRESHOLD: float = 0.5
    # C2: Composite activation threshold
    DEFAULT_COMPOSITE_THRESHOLD: float = 0.7

    # F2 / B2: Score weights for context factors
    _RELEVANCE_WEIGHT: float = 0.5
    _RECENCY_WEIGHT: float = 0.8
    _ACTIVATION_FREQ_WEIGHT: float = 1.0
    _UNIQUENESS_WEIGHT: float = 0.9

    def compute_forward_wing(
        self,
        content: str,
        temporal_context: list[str],
        entity_context: list[str],
        thematic_context: list[str],
        recency: float = 0.8,
        activation_freq: float = 1.0,
    ) -> tuple[WingMetadata, float, list[str]]:
        """Compute the forward wing (inductive/generalization).

        F1: Creates WingMetadata for the forward wing.
        F2: Computes association score = relevance * recency * activation_freq.
        F3: Uses forward activation threshold (0.5).
        F4: Determines trigger type (temporal/entity/thematic).
        F5: Returns associations as temporal + entity context.
        F6: Caching is handled by the caller.

        Args:
            content: The memory content being processed.
            temporal_context: Time-related associated concepts.
            entity_context: Entity-related associated concepts.
            thematic_context: Theme-related associated concepts.
            recency: Recency factor 0-1 (default 0.8).
            activation_freq: How frequently this pattern activates (default 1.0).

        Returns:
            Tuple of (WingMetadata, score, list of associations).
        """
        # F1: Build forward wing metadata
        metadata = WingMetadata(wing=WingType.FORWARD)

        # F4: Determine trigger type based on available context
        if temporal_context:
            metadata.trigger_type = "temporal"
        elif entity_context:
            metadata.trigger_type = "entity"
        elif thematic_context:
            metadata.trigger_type = "thematic"

        # F2: Association score = relevance * recency * activation_freq
        # Simple relevance based on how much context we have
        relevance = self._RELEVANCE_WEIGHT
        score = relevance * recency * activation_freq

        # F1: Strength and confidence from score
        metadata.strength = min(score, 1.0)
        metadata.confidence = score

        # F5: Output format — temporal + entity associations
        associations = list(temporal_context) + list(entity_context)
        if thematic_context and not associations:
            associations = list(thematic_context)

        metadata.association_desc = f"forward[{metadata.trigger_type}]: {', '.join(associations[:3])}"

        return metadata, score, associations

    def compute_backward_wing(
        self,
        content: str,
        citations: int,
        usage_context: list[str],
        recency: float = 0.8,
    ) -> tuple[WingMetadata, float, list[str]]:
        """Compute the backward wing (anchor/verification).

        B1: Creates WingMetadata for the backward wing.
        B2: Computes association score = min(citations*0.1, 1.0) * recency * uniqueness.
        B3: Uses backward activation threshold (0.5).
        B4: Determines trigger type (citation-based if citations > 0).
        B5: Returns associations as usage context.
        B6: Caching is handled by the caller.

        Args:
            content: The memory content being processed.
            citations: Number of times this memory was cited/referenced.
            usage_context: Context of how this memory has been used.
            recency: Recency factor 0-1 (default 0.8).

        Returns:
            Tuple of (WingMetadata, score, list of associations).
        """
        # B1: Build backward wing metadata
        metadata = WingMetadata(wing=WingType.BACKWARD)

        # B4: Trigger type is citation-based
        if citations > 0:
            metadata.trigger_type = "citation"
        elif usage_context:
            metadata.trigger_type = "usage"

        # B2: Association score = min(citations*0.1, 1.0) * recency * uniqueness
        citation_score = min(citations * 0.1, 1.0)
        score = citation_score * recency * self._UNIQUENESS_WEIGHT

        # B1: Strength and confidence from score
        metadata.strength = min(score, 1.0)
        metadata.confidence = score

        # B5: Output format — usage context associations
        associations = list(usage_context)
        metadata.association_desc = f"backward[{metadata.trigger_type}]: {', '.join(associations[:3])}"

        return metadata, score, associations

    def compute_composite_score(
        self,
        forward_score: float,
        backward_score: float,
        balance: tuple[float, float] = (1.0, 1.0),
    ) -> float:
        """C1: Compute the bidirectional composite score.

        Weights forward and backward scores by the balance ratio.

        Args:
            forward_score: Score from forward wing (0-1).
            backward_score: Score from backward wing (0-1).
            balance: Tuple of (forward_weight, backward_weight).

        Returns:
            Weighted composite score (0-1).
        """
        total = balance[0] + balance[1]
        if total == 0:
            return 0.0
        return (forward_score * balance[0] + backward_score * balance[1]) / total

    def is_wing_active(
        self,
        composite_score: float,
        threshold: float | None = None,
    ) -> bool:
        """C2: Determine if the butterfly wing is activated.

        A wing is active when its composite score exceeds the threshold.

        Args:
            composite_score: The computed composite score.
            threshold: Activation threshold (default 0.7).

        Returns:
            True if composite_score > threshold.
        """
        if threshold is None:
            threshold = self.DEFAULT_COMPOSITE_THRESHOLD
        return composite_score > threshold

    def score_memory(
        self,
        content: str,
        temporal_context: list[str] | None = None,
        entity_context: list[str] | None = None,
        thematic_context: list[str] | None = None,
        citations: int = 0,
        usage_context: list[str] | None = None,
        recency: float = 0.8,
        balance: tuple[float, float] = (1.0, 1.0),
    ) -> ButterflyWing:
        """Compute complete butterfly wing for a memory item.

        Convenience method that runs both forward and backward computation,
        then combines them into a ButterflyWing.

        Args:
            content: Memory content.
            temporal_context: Forward wing temporal context.
            entity_context: Forward wing entity context.
            thematic_context: Forward wing thematic context.
            citations: Backward wing citation count.
            usage_context: Backward wing usage context.
            recency: Recency factor for both wings.
            balance: (forward_weight, backward_weight) for composite.

        Returns:
            Populated ButterflyWing with all scores and associations.
        """
        tc = temporal_context or []
        ec = entity_context or []
        thc = thematic_context or []
        uc = usage_context or []

        forward_meta, forward_score, forward_assoc = self.compute_forward_wing(
            content=content,
            temporal_context=tc,
            entity_context=ec,
            thematic_context=thc,
            recency=recency,
        )

        backward_meta, backward_score, backward_assoc = self.compute_backward_wing(
            content=content,
            citations=citations,
            usage_context=uc,
            recency=recency,
        )

        composite = self.compute_composite_score(forward_score, backward_score, balance)
        active = self.is_wing_active(composite)

        # C4: TTL = 3 days
        ttl_seconds = 3 * 24 * 3600
        expires = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
        forward_meta.expires_at = expires
        backward_meta.expires_at = expires

        wing = ButterflyWing(
            forward_metadata=forward_meta,
            forward_score=forward_score,
            forward_associations=forward_assoc,
            backward_metadata=backward_meta,
            backward_score=backward_score,
            backward_associations=backward_assoc,
            composite_score=composite,
            is_active=active,
        )

        return wing


# ─────────────────────────────────────────────────────────────────
# ButterflyStore — C4/B3: Persistence layer
# ─────────────────────────────────────────────────────────────────

class ButterflyStore:
    """SQLite-backed persistent store for butterfly wings.

    C4: Butterfly wing lifecycle — 3-day TTL
    B3:  Butterfly wing persistence
    C3:  Debug logging
    C5:  API surface
    C6:  Performance constraints (WAL mode, single writer)
    """

    DEFAULT_TTL_SECONDS: int = 3 * 24 * 3600  # 3 days

    def __init__(self, db_path: str = "data/butterfly.db") -> None:
        self._db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.row_factory = sqlite3.Row

        self._create_tables()
        self._engine = ButterflyEngine()

    def _create_tables(self) -> None:
        """Create butterfly wing storage tables."""
        with self._conn:
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS butterfly_wings (
                    memory_id TEXT PRIMARY KEY,
                    wing_data TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT,
                    forward_score REAL DEFAULT 0.0,
                    backward_score REAL DEFAULT 0.0,
                    composite_score REAL DEFAULT 0.0,
                    is_active INTEGER DEFAULT 0
                )
            """)
            # Index for expiry cleanup
            self._conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_butterfly_expires
                ON butterfly_wings(expires_at)
            """)

    def compute_ttl(self) -> int:
        """C4: Compute butterfly wing TTL (3 days in seconds)."""
        return self.DEFAULT_TTL_SECONDS

    def save_wing(self, memory_id: str, wing: ButterflyWing) -> None:
        """B3/C4: Persist a butterfly wing for a memory ID.

        Args:
            memory_id: The memory item this wing belongs to.
            wing: The computed ButterflyWing.
        """
        now = datetime.now(timezone.utc)
        ttl = self.compute_ttl()
        expires = now + timedelta(seconds=ttl)

        wing_data = json.dumps(wing.to_dict(), ensure_ascii=False)

        with self._conn:
            self._conn.execute(
                """INSERT OR REPLACE INTO butterfly_wings
                   (memory_id, wing_data, created_at, expires_at,
                    forward_score, backward_score, composite_score, is_active)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    memory_id,
                    wing_data,
                    now.isoformat(),
                    expires.isoformat(),
                    wing.forward_score,
                    wing.backward_score,
                    wing.composite_score,
                    1 if wing.is_active else 0,
                ),
            )

    def load_wing(self, memory_id: str) -> Optional[ButterflyWing]:
        """Load a butterfly wing by memory ID.

        Returns None if not found or expired.
        """
        row = self._conn.execute(
            "SELECT * FROM butterfly_wings WHERE memory_id = ?", (memory_id,)
        ).fetchone()

        if row is None:
            return None

        # Check expiry
        expires_str = row["expires_at"]
        if expires_str:
            expires_at = datetime.fromisoformat(expires_str)
            if datetime.now(timezone.utc) > expires_at:
                # C3: Log expired wing cleanup
                self._conn.execute(
                    "DELETE FROM butterfly_wings WHERE memory_id = ?", (memory_id,)
                )
                return None

        wing_data = json.loads(row["wing_data"])
        return ButterflyWing.from_dict(wing_data)

    def delete_wing(self, memory_id: str) -> bool:
        """Delete a butterfly wing by memory ID.

        Returns True if deleted, False if not found.
        """
        with self._conn:
            cursor = self._conn.execute(
                "DELETE FROM butterfly_wings WHERE memory_id = ?", (memory_id,)
            )
        return cursor.rowcount > 0

    def cleanup_expired(self) -> int:
        """Remove all expired butterfly wings.

        C4: Lifecycle management — removes expired entries.

        Returns:
            Number of entries removed.
        """
        now = datetime.now(timezone.utc).isoformat()
        with self._conn:
            cursor = self._conn.execute(
                "DELETE FROM butterfly_wings WHERE expires_at IS NOT NULL AND expires_at < ?",
                (now,),
            )
        return cursor.rowcount

    def get_wing_details(self, memory_id: str) -> dict:
        """C5: Return wing details as a dict (API surface).

        Returns:
            Dict with forward and backward wing details.
        """
        wing = self.load_wing(memory_id)
        if wing is None:
            return {"forward": [], "backward": []}

        return {
            "forward": [
                {
                    "score": wing.forward_score,
                    "associations": wing.forward_associations,
                    "metadata": {
                        "strength": wing.forward_metadata.strength,
                        "confidence": wing.forward_metadata.confidence,
                        "trigger_type": wing.forward_metadata.trigger_type,
                        "expires_at": (
                            wing.forward_metadata.expires_at.isoformat()
                            if wing.forward_metadata.expires_at else None
                        ),
                    },
                }
            ],
            "backward": [
                {
                    "score": wing.backward_score,
                    "associations": wing.backward_associations,
                    "metadata": {
                        "strength": wing.backward_metadata.strength,
                        "confidence": wing.backward_metadata.confidence,
                        "trigger_type": wing.backward_metadata.trigger_type,
                        "expires_at": (
                            wing.backward_metadata.expires_at.isoformat()
                            if wing.backward_metadata.expires_at else None
                        ),
                    },
                }
            ],
        }

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()


# ─────────────────────────────────────────────────────────────────
# ButterflyRecallStrategy — C5: Recall strategy with wing filtering
# ─────────────────────────────────────────────────────────────────

class ButterflyRecallStrategy:
    """Recall strategy that filters memory items by butterfly wing.

    C5: Butterfly wing API — filters recall results by wing type.

    This strategy wraps a base recall strategy and applies butterfly
    wing filtering on top of the results.
    """

    def __init__(
        self,
        base_recall,  # RecallStrategy | MemoryService
        butterfly_store: ButterflyStore,
        engine: ButterflyEngine | None = None,
    ) -> None:
        self._base_recall = base_recall
        self._store = butterfly_store
        self._engine = engine or ButterflyEngine()

    async def recall(
        self,
        query: str,
        agent_id: str = "",
        session_id: str = "",
        memory_type=None,
        scope=None,
        top_k: int = 10,
        wing: Literal["forward", "backward"] | None = None,
        threshold: float = 0.7,
    ) -> list:
        """Recall memories with optional butterfly wing filtering.

        B5: Butterfly wing API — filters by wing type and threshold.

        Args:
            query: Search query.
            agent_id: Agent filter.
            session_id: Session filter.
            memory_type: Memory type filter.
            scope: Scope filter.
            top_k: Maximum results.
            wing: Filter by "forward", "backward", or None (both).
            threshold: Minimum composite score threshold (default 0.7).

        Returns:
            List of memory items filtered by wing criteria.
        """
        # Get base recall results
        if hasattr(self._base_recall, "recall"):
            items = await self._base_recall.recall(
                query=query,
                agent_id=agent_id,
                session_id=session_id,
                memory_type=memory_type,
                scope=scope,
                top_k=top_k * 2,  # Over-fetch since we'll filter
            )
        else:
            items = []

        result: list = []
        for item in items:
            # Support both dict items and objects with .id
            item_id = item.get("id") if isinstance(item, dict) else getattr(item, "id", None)
            if item_id is None:
                continue
            wing_data = self._store.load_wing(item_id)
            if wing_data is None:
                # No butterfly wing yet — include by default
                if wing is None:
                    result.append(item)
                continue

            # Check composite threshold
            if wing_data.composite_score < threshold:
                continue

            # Filter by specific wing
            if wing == "forward":
                if wing_data.forward_score > 0.5:
                    result.append(item)
            elif wing == "backward":
                if wing_data.backward_score > 0.5:
                    result.append(item)
            else:
                # Both wings — include if either passes threshold
                result.append(item)

            if len(result) >= top_k:
                break

        return result[:top_k]

    def get_wing_details(self, memory_id: str) -> dict:
        """B5: Return wing details for a specific memory item."""
        return self._store.get_wing_details(memory_id)


# ─────────────────────────────────────────────────────────────────
# Module-level convenience factory
# ─────────────────────────────────────────────────────────────────

_default_store: ButterflyStore | None = None


def get_default_store(db_path: str = "data/butterfly.db") -> ButterflyStore:
    """Get or create the default global ButterflyStore instance."""
    global _default_store
    if _default_store is None:
        _default_store = ButterflyStore(db_path=db_path)
    return _default_store
