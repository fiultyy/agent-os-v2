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
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Literal, Optional

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
        *,
        kg: Any | None = None,
        forward_relevance: float | None = None,
        entity_ids: list[str] | None = None,
    ) -> tuple[WingMetadata, float, list[str]]:
        """Compute the forward wing (inductive/generalization).

        F1: Creates WingMetadata for the forward wing.
        F2: Computes association score = relevance * recency * activation_freq.
        F3: Uses forward activation threshold (0.5).
        F4: Determines trigger type (temporal/entity/thematic).
        F5: Returns associations as entity IDs when available (Part 2 ⑦:
            from string-concatenation → entity ID list), else temporal +
            entity context strings for backward compatibility.
        F6: Caching is handled by the caller.

        Part 2 ⑦ relevance provenance (审查修正：只改本方法**局部取值**，
        绝不动类常量 ``_RELEVANCE_WEIGHT``——它被 backward wing 复用，改了污染):

          relevance 来源优先级：
            1. 显式 ``forward_relevance``（调用方直接给值，最高优先）。
            2. ``kg`` + ``entity_ids`` / ``entity_context``：查 KG relations
               表中相关边的 ``confidence`` 均值（真实联想强度，Hebbian/LTP 学出来的）。
            3. 退化：类常量 ``_RELEVANCE_WEIGHT`` (0.5)——保持现状，老调用者零回归。

        Args:
            content: The memory content being processed.
            temporal_context: Time-related associated concepts.
            entity_context: Entity-related associated concepts.
            thematic_context: Theme-related associated concepts.
            recency: Recency factor 0-1 (default 0.8).
            activation_freq: How frequently this pattern activates (default 1.0).
            kg: Optional KG handle to read relations.confidence. KG 满足
                ``_resolve_entity_id`` / ``get_entity_relations``（KnowledgeGraph
                天然满足）。None → 退化到 ``_RELEVANCE_WEIGHT``。
            forward_relevance: Optional explicit relevance value. Wins over KG
                lookup when provided.
            entity_ids: Optional explicit entity IDs to look up in the KG.
                Falls back to ``entity_context`` (treated as names/IDs) when
                omitted. Drives both relevance lookup and the F5 output list
                (entity IDs replace the old string-concatenation).

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
        # Part 2 ⑦: relevance 从 KG relations.confidence 取（局部取值，
        # 不动类常量 _RELEVANCE_WEIGHT）。无 KG / 无边 → 退化到常量。
        relevance = self._resolve_forward_relevance(
            kg=kg,
            forward_relevance=forward_relevance,
            entity_ids=entity_ids,
            entity_context=entity_context,
        )
        score = relevance * recency * activation_freq

        # F1: Strength and confidence from score
        metadata.strength = min(score, 1.0)
        metadata.confidence = score

        # F5: Output format — Part 2 ⑦ entity ID list when entity_ids given,
        # else legacy temporal + entity string-concatenation (backward compat).
        if entity_ids:
            associations = [eid for eid in entity_ids if eid]
            if not associations:
                associations = list(temporal_context) + list(entity_context)
        else:
            associations = list(temporal_context) + list(entity_context)
        if thematic_context and not associations:
            associations = list(thematic_context)

        metadata.association_desc = f"forward[{metadata.trigger_type}]: {', '.join(associations[:3])}"

        return metadata, score, associations

    def _resolve_forward_relevance(
        self,
        *,
        kg: Any | None,
        forward_relevance: float | None,
        entity_ids: list[str] | None,
        entity_context: list[str],
    ) -> float:
        """Part 2 ⑦: resolve forward-wing relevance from KG relations.confidence.

        审查修正（critical/high）：
          - **只改 compute_forward_wing 局部取值**，绝不动类常量
            ``_RELEVANCE_WEIGHT``（被 backward wing 复用）。
          - KG 不可用 / 查不到边 → 退化到 ``_RELEVANCE_WEIGHT``（零回归）。
          - relevance clamp 到 ``[0, 1]``，防 confidence > 1 越界。

        Returns a float relevance ``∈ [0, 1]``.
        """
        # 1. 显式注入优先
        if forward_relevance is not None:
            return float(max(0.0, min(1.0, forward_relevance)))

        # 2. 无 KG → 退化到类常量
        if kg is None:
            return self._RELEVANCE_WEIGHT

        # 3. 从 KG relations.confidence 取相关边均值
        candidates = list(entity_ids) if entity_ids else list(entity_context)
        if not candidates:
            return self._RELEVANCE_WEIGHT

        confidences: list[float] = []
        for name_or_id in candidates:
            if not name_or_id:
                continue
            try:
                eid = kg._resolve_entity_id(name_or_id)
            except Exception:
                eid = None
            if not eid:
                continue
            try:
                rels = kg.get_entity_relations(eid)
            except Exception:
                rels = []
            for r in rels or []:
                try:
                    confidences.append(float(r.get("confidence") or 0.0))
                except (TypeError, ValueError):
                    continue

        if not confidences:
            # KG 有但无边 → 退化到类常量（不让 score 因 0 relevance 全灭）
            return self._RELEVANCE_WEIGHT

        return float(max(0.0, min(1.0, sum(confidences) / len(confidences))))

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

    Part 2 ⑦ reform (审查修正 high)：recall 不再是纯过滤——当 ``weighted=True``
    且提供神经场 ``field`` / ``lif_state`` 时，改用 **match × lif** 乘积排序
    （``score = match_item(query, item) × lif_item(field, item.涉及概念)``）。
    score 对 **memory item** 算（非"关系组 g"）。wing 过滤作为正向筛选保留，
    backward wing 路径不受影响（见 :meth:`_passes_wing_filter`）。

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
        *,
        weighted: bool = False,
        lif_state: Any | None = None,
        field: dict[str, float] | None = None,
    ) -> list:
        """Recall memories with optional butterfly wing filtering / match×lif ranking.

        B5: Butterfly wing API — filters by wing type and threshold.

        Part 2 ⑦: when ``weighted=True`` and a neural ``field`` /
        ``lif_state`` is provided, items are **re-ranked by
        ``match × lif``** (``rank_items``). The wing filter is still
        applied as a positive screen (so backward-wing items stay
        anchor-only, forward-wing items stay inductive-only), but the
        ordering is now score-driven instead of base-recall order.

        Args:
            query: Search query.
            agent_id: Agent filter.
            session_id: Session filter.
            memory_type: Memory type filter.
            scope: Scope filter.
            top_k: Maximum results.
            wing: Filter by "forward", "backward", or None (both).
            threshold: Minimum composite score threshold (default 0.7).
            weighted: Part 2 ⑦ switch — enable ``match × lif`` re-ranking.
                Default ``False`` ⇒ legacy pure-filter behaviour (zero
                regression for existing callers / tests).
            lif_state: Optional neural state (any object exposing a
                ``field: dict[str, float]`` attribute, e.g. NeuralState).
                Ignored unless ``weighted=True``.
            field: Optional explicit potential field override. Wins over
                ``lif_state.field`` when both are given.

        Returns:
            List of memory items filtered by wing criteria, optionally
            re-ordered by ``match × lif`` score.
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
            if self._passes_wing_filter(item, wing, threshold):
                result.append(item)
                if len(result) >= top_k * 2:  # over-collect then re-rank/truncate
                    break

        if not weighted:
            return result[:top_k]

        # Part 2 ⑦: match × lif re-ranking.
        field_map = field if field is not None else self._extract_field(lif_state)
        return self._rank_by_match_lif(result, query, field_map, top_k)

    # ── wing filtering (extracted, backward-compatible) ────────────
    def _passes_wing_filter(
        self,
        item: Any,
        wing: Literal["forward", "backward"] | None,
        threshold: float,
    ) -> bool:
        """Legacy wing-filter predicate (review correction: backward wing
        path is byte-for-byte preserved — see ``test_backward_wing_unaffected``)."""
        item_id = item.get("id") if isinstance(item, dict) else getattr(item, "id", None)
        if item_id is None:
            return False
        wing_data = self._store.load_wing(item_id)
        if wing_data is None:
            # No butterfly wing yet — include by default (only when both wings)
            return wing is None

        # Check composite threshold
        if wing_data.composite_score < threshold:
            return False

        # Filter by specific wing
        if wing == "forward":
            return wing_data.forward_score > 0.5
        if wing == "backward":
            return wing_data.backward_score > 0.5
        # Both wings — include if either passes threshold
        return True

    # ── Part 2 ⑦ match × lif ranking ───────────────────────────────
    @staticmethod
    def _extract_field(lif_state: Any | None) -> dict[str, float]:
        if lif_state is None:
            return {}
        try:
            field = getattr(lif_state, "field", None) or {}
        except Exception:  # pragma: no cover - defensive
            return {}
        return dict(field)

    def _rank_by_match_lif(
        self,
        items: list,
        query: str,
        field: dict[str, float],
        top_k: int,
    ) -> list:
        """Re-rank filtered items by ``match_item × lif_item``.

        Items come back from base recall as either ``MemoryItem`` objects
        (preferred — ``rank_items`` uses ``item.content``) or dict items
        (we coerce to a lightweight stand-in). Missing field ⇒ falls
        back to pure match order (Part 1 equivalent).
        """
        if not items:
            return []

        from src.memory._recall.weighted_recall import rank_items

        # Convert dict items to a MemoryItem-like object so weighted_recall
        # (which reads item.content / item.id) can operate uniformly.
        MemoryItem = _get_memory_item_cls()
        memory_items: list = []
        for it in items:
            if isinstance(it, dict):
                memory_items.append(MemoryItem(
                    id=it.get("id", ""),
                    content=it.get("content", ""),
                ))
            else:
                memory_items.append(it)

        ranked = rank_items(memory_items, query, field or None, top_k=top_k)
        # Map back to the original item objects (prefer original so callers
        # see the same item instances they passed in).
        original_by_id: dict[str, Any] = {}
        for it in items:
            iid = it.get("id") if isinstance(it, dict) else getattr(it, "id", None)
            if iid is not None:
                original_by_id[iid] = it

        out: list = []
        for r in ranked:
            scored_item = r["item"]
            sid = getattr(scored_item, "id", None)
            out.append(original_by_id.get(sid, scored_item))
        return out

    def get_wing_details(self, memory_id: str) -> dict:
        """B5: Return wing details for a specific memory item."""
        return self._store.get_wing_details(memory_id)


def _get_memory_item_cls():
    """Lazy import of MemoryItem to avoid a hard top-level dependency
    (butterfly_wing is a low-level module imported broadly)."""
    from src.memory.types import MemoryItem
    return MemoryItem


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
