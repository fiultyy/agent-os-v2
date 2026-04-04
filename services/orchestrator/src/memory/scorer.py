"""Importance scorer — five-dimensional weighted scoring for memory items.

Dimensions:
- **recency**: How recently the memory was created/accessed (exponential decay).
- **frequency**: How often the memory has been accessed.
- **relevance**: How relevant to the current task/query context.
- **emotional_weight**: Emotional significance of the content.
- **actionability**: Whether the content contains actionable information.

Each dimension is scored [0, 1], then combined with configurable weights.
Templates are provided for different agent roles.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from src.memory.types import MemoryItem


# ── Weight templates ────────────────────────────────────────────────

@dataclass
class WeightTemplate:
    """Configurable weight set for the five scoring dimensions."""
    recency: float = 0.25
    frequency: float = 0.15
    relevance: float = 0.25
    emotional_weight: float = 0.15
    actionability: float = 0.20

    def __post_init__(self):
        total = self.recency + self.frequency + self.relevance + self.emotional_weight + self.actionability
        if abs(total - 1.0) > 0.01:
            raise ValueError(f"Weights must sum to 1.0, got {total}")


class TemplatePreset(str, Enum):
    RESEARCH = "research"
    CODING = "coding"
    GENERAL = "general"


PRESETS: dict[TemplatePreset, WeightTemplate] = {
    TemplatePreset.RESEARCH: WeightTemplate(
        recency=0.15, frequency=0.10, relevance=0.40,
        emotional_weight=0.10, actionability=0.25,
    ),
    TemplatePreset.CODING: WeightTemplate(
        recency=0.20, frequency=0.20, relevance=0.30,
        emotional_weight=0.05, actionability=0.25,
    ),
    TemplatePreset.GENERAL: WeightTemplate(
        recency=0.25, frequency=0.15, relevance=0.25,
        emotional_weight=0.15, actionability=0.20,
    ),
}


# ── Scoring functions ───────────────────────────────────────────────

def score_recency(
    item: MemoryItem,
    half_life_hours: float = 168.0,  # 7 days default
) -> float:
    """Exponential decay based on age.

    ``score = e^(-lambda * age_hours)`` where ``lambda = ln(2) / half_life``.

    Args:
        item: Memory item to score.
        half_life_hours: Hours for score to decay to 0.5.

    Returns:
        Score in (0, 1].
    """
    now = datetime.now(timezone.utc)
    created = datetime.fromisoformat(item.created_at)
    age_hours = max(0, (now - created).total_seconds() / 3600)
    lam = math.log(2) / half_life_hours
    return math.exp(-lam * age_hours)


def score_frequency(item: MemoryItem) -> float:
    """Score based on access frequency.

    Uses ``access_count`` from metadata if available, otherwise
    infers from creation vs access time difference.
    """
    access_count = item.metadata.get("access_count", 0)
    if access_count > 0:
        # Saturating curve: diminishing returns after many accesses
        return 1.0 - math.exp(-access_count / 5.0)
    return 0.1  # Default low score for unaccessed items


def score_relevance(item: MemoryItem, query: str = "") -> float:
    """Score based on content relevance to a query.

    If no query is provided, uses the item's base importance as a proxy.
    """
    if not query.strip():
        return item.importance

    # Simple keyword overlap score
    query_words = set(query.lower().split())
    content_words = set(item.content.lower().split())
    if not query_words:
        return item.importance

    overlap = len(query_words & content_words)
    ratio = overlap / len(query_words)
    return min(1.0, ratio)


# Emotional weight markers
_EMOTIONAL_POSITIVE = re.compile(
    r'\b(important|critical|urgent|essential|vital|crucial|breakthrough|love|hate'
    r'|fear|angry|happy|sad|surprised|amazing|terrible|wonderful|awful'
    r'|重要|关键|紧急|必须|危险|惊讶|失望|兴奋|愤怒|恐惧)\b',
    re.IGNORECASE,
)

_ACTIONABLE = re.compile(
    r'\b(should|must|need to|todo|fix|implement|deploy|create|update|delete'
    r'|add|remove|change|configure|setup|install|run|execute|test'
    r'|应该|必须|需要|修复|部署|创建|更新|删除|执行|测试)\b',
    re.IGNORECASE,
)


def score_emotional_weight(item: MemoryItem) -> float:
    """Score based on emotional significance of content.

    Uses keyword heuristics to detect emotional language.
    """
    matches = _EMOTIONAL_POSITIVE.findall(item.content)
    if not matches:
        return 0.2  # Neutral default
    # More emotional words = higher score, saturating
    return min(1.0, 0.3 + 0.15 * len(matches))


def score_actionability(item: MemoryItem) -> float:
    """Score based on whether content contains actionable information."""
    matches = _ACTIONABLE.findall(item.content)
    if not matches:
        return 0.1
    return min(1.0, 0.3 + 0.15 * len(matches))


# ── Main scorer ─────────────────────────────────────────────────────

@dataclass
class ImportanceScore:
    """Result of importance scoring with breakdown."""
    total: float
    recency: float = 0.0
    frequency: float = 0.0
    relevance: float = 0.0
    emotional_weight: float = 0.0
    actionability: float = 0.0


class ImportanceScorer:
    """Five-dimensional weighted importance scorer.

    Usage::

        scorer = ImportanceScorer(preset="coding")
        score = scorer.score(item, query="deploy the service")
    """

    def __init__(
        self,
        preset: str | TemplatePreset = TemplatePreset.GENERAL,
        weights: WeightTemplate | None = None,
        half_life_hours: float = 168.0,
        forget_threshold: float = 0.1,
    ) -> None:
        if isinstance(preset, str):
            preset = TemplatePreset(preset)
        self._weights = weights or PRESETS.get(preset, PRESETS[TemplatePreset.GENERAL])
        self._half_life = half_life_hours
        self._forget_threshold = forget_threshold

    @property
    def weights(self) -> WeightTemplate:
        return self._weights

    @property
    def forget_threshold(self) -> float:
        return self._forget_threshold

    def score(
        self,
        item: MemoryItem,
        query: str = "",
    ) -> ImportanceScore:
        """Compute the importance score for a memory item.

        Args:
            item: Memory item to score.
            query: Optional context query for relevance scoring.

        Returns:
            :class:`ImportanceScore` with total and per-dimension breakdown.
        """
        r = score_recency(item, self._half_life)
        f = score_frequency(item)
        rel = score_relevance(item, query)
        e = score_emotional_weight(item)
        a = score_actionability(item)

        w = self._weights
        total = (
            w.recency * r
            + w.frequency * f
            + w.relevance * rel
            + w.emotional_weight * e
            + w.actionability * a
        )

        return ImportanceScore(
            total=round(total, 4),
            recency=round(r, 4),
            frequency=round(f, 4),
            relevance=round(rel, 4),
            emotional_weight=round(e, 4),
            actionability=round(a, 4),
        )

    def should_forget(self, item: MemoryItem, query: str = "") -> bool:
        """Check if a memory item should be marked as a forget candidate."""
        s = self.score(item, query)
        return s.total < self._forget_threshold
