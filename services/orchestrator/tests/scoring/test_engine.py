"""Tests for ScoringEngine."""
import pytest
from src.scoring import ScoringEngine, ScoringPolicy, ScoringContext, ScoringSignal


class DummyPolicy(ScoringPolicy):
    """Dummy policy for testing."""
    
    def __init__(self, name: str = "dummy", trigger: bool = False, score: float = 0.5):
        self.name = name
        self._trigger = trigger
        self._score = score
    
    def evaluate(self, context: ScoringContext) -> ScoringSignal:
        return ScoringSignal(
            trigger=self._trigger,
            score=self._score,
            confidence=0.8,
            breakdown={self.name: self._score},
            reason=f"dummy: {self._trigger}",
            metadata={"policy": self.name}
        )
    
    def get_threshold(self) -> float:
        return 0.5


def test_engine_any_mode_trigger():
    """Test 'any' mode triggers if any policy triggers."""
    engine = ScoringEngine(
        policies=[DummyPolicy("p1", trigger=False, score=0.3), DummyPolicy("p2", trigger=True, score=0.8)],
        combine_mode="any"
    )
    result = engine.evaluate(ScoringContext(nodes=[]))
    assert result.trigger is True
    assert result.score == 0.8


def test_engine_all_mode_requires_all():
    """Test 'all' mode requires all policies to trigger."""
    engine = ScoringEngine(
        policies=[DummyPolicy("p1", trigger=True, score=0.3), DummyPolicy("p2", trigger=False, score=0.8)],
        combine_mode="all"
    )
    result = engine.evaluate(ScoringContext(nodes=[]))
    assert result.trigger is False


def test_engine_max_mode():
    """Test 'max' mode uses maximum score."""
    engine = ScoringEngine(
        policies=[DummyPolicy("p1", trigger=False, score=0.3), DummyPolicy("p2", trigger=False, score=0.8)],
        combine_mode="max"
    )
    result = engine.evaluate(ScoringContext(nodes=[]))
    assert result.trigger is False
    assert result.score == 0.8


def test_engine_weighted_mode():
    """Test 'weighted' mode computes weighted average."""
    engine = ScoringEngine(
        policies=[DummyPolicy("p1", trigger=False, score=0.5), DummyPolicy("p2", trigger=False, score=1.0)],
        combine_mode="weighted",
        weights={"p1": 1.0, "p2": 3.0}
    )
    result = engine.evaluate(ScoringContext(nodes=[]))
    # (0.5*1 + 1.0*3) / 4 = 3.5/4 = 0.875
    assert abs(result.score - 0.875) < 0.001


def test_engine_add_policy():
    """Test adding policy at runtime."""
    engine = ScoringEngine(policies=[DummyPolicy("p1")])
    engine.add_policy(DummyPolicy("p2"), weight=2.0)
    assert len(engine.policies) == 2
    assert engine.weights["p2"] == 2.0
