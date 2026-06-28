"""ScoringEngine — multi-strategy scoring engine."""
from __future__ import annotations
from dataclasses import dataclass, field

from .policy import ScoringPolicy, ScoringContext, ScoringSignal


@dataclass
class ScoringEngine:
    """
    Unified scoring engine with pluggable policies.
    
    Supports multiple combine modes:
    - "any": Trigger if any policy triggers (sensitive, good for early iteration)
    - "all": Trigger only if all policies trigger (conservative)
    - "weighted": Weighted average of scores
    - "max": Maximum score across policies
    """
    
    policies: list[ScoringPolicy] = field(default_factory=list)
    combine_mode: str = "any"  # "any" | "all" | "weighted" | "max"
    weights: dict[str, float] = field(default_factory=dict)  # policy name -> weight
    
    def __post_init__(self) -> None:
        # Initialize weights for all policies
        for p in self.policies:
            if p.name not in self.weights:
                self.weights[p.name] = 1.0
    
    def set_weights(self, weights: dict[str, float]) -> None:
        """Dynamically adjust policy weights."""
        self.weights.update(weights)
    
    def add_policy(self, policy: ScoringPolicy, weight: float = 1.0) -> None:
        """Add a new policy at runtime."""
        self.policies.append(policy)
        self.weights[policy.name] = weight
    
    def evaluate(self, context: ScoringContext) -> ScoringSignal:
        """Evaluate all policies and return combined signal."""
        signals = [p.evaluate(context) for p in self.policies]
        
        if not signals:
            return ScoringSignal(
                trigger=False, score=0.0, confidence=0.0,
                breakdown={}, reason="no policies", metadata={}
            )
        
        if self.combine_mode == "any":
            trigger = any(s.trigger for s in signals)
            score = max(s.score for s in signals)
            confidence = max(s.confidence for s in signals)
        elif self.combine_mode == "all":
            trigger = all(s.trigger for s in signals)
            score = min(s.score for s in signals)
            confidence = min(s.confidence for s in signals)
        elif self.combine_mode == "weighted":
            total_weight = sum(self.weights.get(s.metadata.get("policy", ""), 1.0) for s in signals)
            score = sum(s.score * self.weights.get(s.metadata.get("policy", ""), 1.0) for s in signals) / max(total_weight, 0.001)
            confidence = sum(s.confidence * self.weights.get(s.metadata.get("policy", ""), 1.0) for s in signals) / max(total_weight, 0.001)
            trigger = any(s.trigger for s in signals)
        else:  # max
            trigger = any(s.trigger for s in signals)
            score = max(s.score for s in signals)
            confidence = max(s.confidence for s in signals)
        
        breakdown = {s.metadata.get("policy", f"policy_{i}"): s.score for i, s in enumerate(signals)}
        return ScoringSignal(
            trigger=trigger,
            score=score,
            confidence=confidence,
            breakdown=breakdown,
            reason=f"combine_mode={self.combine_mode}, triggered={trigger}",
            metadata={"policies": [p.name for p in self.policies], "combine_mode": self.combine_mode}
        )
