"""ReuseThresholdPolicy — reuse score based scoring."""
from __future__ import annotations
from dataclasses import dataclass

from ..policy import ScoringPolicy, ScoringContext, ScoringSignal


@dataclass
class ReuseThresholdPolicy:
    """
    Reuse threshold scoring policy.
    
    Triggers when max reuse_score >= threshold and min_node_count reached.
    """
    
    name: str = "reuse_threshold"
    reuse_threshold: float = 0.7
    min_node_count: int = 5
    
    def evaluate(self, context: ScoringContext) -> ScoringSignal:
        if not context.nodes:
            return ScoringSignal(
                trigger=False, score=0.0, confidence=1.0,
                breakdown={}, reason="no nodes", metadata={"policy": self.name}
            )
        
        # Extract reuse_score from nodes
        reuse_scores = [
            float(n.get("reuse_score", 0.0)) for n in context.nodes
        ]
        
        if not reuse_scores:
            return ScoringSignal(
                trigger=False, score=0.0, confidence=1.0,
                breakdown={}, reason="no reuse scores",
                metadata={"policy": self.name}
            )
        
        max_reuse = max(reuse_scores)
        avg_reuse = sum(reuse_scores) / len(reuse_scores)
        
        trigger = (
            max_reuse >= self.reuse_threshold and
            len(context.nodes) >= self.min_node_count
        )
        
        # Normalize score to [0, 1]
        score = min(avg_reuse / self.reuse_threshold, 1.0)
        confidence = min(len(context.nodes) / max(self.min_node_count, 1), 1.0)
        
        return ScoringSignal(
            trigger=trigger,
            score=score,
            confidence=confidence,
            breakdown={
                "avg_reuse": avg_reuse,
                "max_reuse": max_reuse,
                "node_count": len(context.nodes),
            },
            reason=f"reuse: avg={avg_reuse:.2f}, max={max_reuse:.2f}, count={len(context.nodes)}",
            metadata={"policy": self.name}
        )
    
    def get_threshold(self) -> float:
        return self.reuse_threshold
