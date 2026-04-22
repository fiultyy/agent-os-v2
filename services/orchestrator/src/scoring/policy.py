"""ScoringPolicy Protocol and data structures."""
from __future__ import annotations
from typing import Protocol, runtime_checkable, Any
from dataclasses import dataclass, field


@dataclass
class ScoringSignal:
    """Unified scoring signal format returned by all ScoringPolicies."""
    trigger: bool                    # Whether evolution should trigger
    score: float                    # Composite score 0-1
    confidence: float             # Confidence 0-1
    breakdown: dict[str, float]    # Per-dimension breakdown
    reason: str                    # Human-readable reason
    metadata: dict[str, Any]       # Additional metadata

    def __post_init__(self) -> None:
        self.score = max(0.0, min(1.0, self.score))
        self.confidence = max(0.0, min(1.0, self.confidence))


@dataclass
class ScoringContext:
    """Context passed to ScoringPolicy.evaluate()."""
    nodes: list[dict[str, Any]] = field(default_factory=list)
    forward_wings: list[dict[str, Any]] = field(default_factory=list)
    backward_wings: list[dict[str, Any]] = field(default_factory=list)
    graph_stats: dict[str, Any] | None = None  # {"out_degree": int, "in_degree": int, "pagerank": float, "cluster_coef": float}
    time_window_hours: int = 24
    domain: str | None = None
    agent_id: str | None = None
    metadata: dict[str, Any] | None = None


@runtime_checkable
class ScoringPolicy(Protocol):
    """Protocol for scoring policies.
    
    All scoring policies must implement this protocol.
    ScoringEngine calls evaluate() with a ScoringContext and receives a ScoringSignal.
    """
    
    name: str  # Policy name e.g. "butterfly_signal", "reuse_threshold"
    
    def evaluate(self, context: ScoringContext) -> ScoringSignal:
        """Evaluate context and return a scoring signal."""
        ...
    
    def get_threshold(self) -> float:
        """Return current trigger threshold (configurable)."""
        ...
