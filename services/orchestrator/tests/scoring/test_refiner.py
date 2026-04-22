"""Tests for RefinerAgent and ArchitectAgent."""
import pytest


def test_refiner_output_schema():
    """Refiner output matches REFINER_SPEC.output_schema."""
    from src.scoring.agents import RefinerAgent
    from src.scoring.policy import ScoringContext, ScoringSignal
    from src.memory.experience_kg import ExperienceKG

    kg = ExperienceKG(db_path=":memory:")
    agent = RefinerAgent(experience_kg=kg, reuse_threshold=0.5, min_bundle_nodes=2)

    ctx = ScoringContext(
        nodes=[
            {"id": "n1", "content": "use kubectl debug", "reuse_score": 0.8, "skill_domain": "k8s"},
            {"id": "n2", "content": "kubectl apply -f", "reuse_score": 0.9, "skill_domain": "k8s"},
        ],
        domain="k8s",
    )
    signal = ScoringSignal(trigger=True, score=0.7, confidence=0.8, breakdown={}, reason="test", metadata={})

    result = agent.process(ctx, signal)

    assert "new_skill_bundles" in result
    assert "retired_bundles" in result
    assert "merge_decisions" in result


def test_refiner_insufficient_nodes():
    """Refiner returns empty when not enough high-reuse nodes."""
    from src.scoring.agents import RefinerAgent
    from src.scoring.policy import ScoringContext, ScoringSignal
    from src.memory.experience_kg import ExperienceKG

    kg = ExperienceKG(db_path=":memory:")
    agent = RefinerAgent(experience_kg=kg, reuse_threshold=0.9, min_bundle_nodes=5)

    ctx = ScoringContext(
        nodes=[
            {"id": "n1", "content": "test", "reuse_score": 0.3},
        ],
    )
    signal = ScoringSignal(trigger=True, score=0.3, confidence=0.5, breakdown={}, reason="test", metadata={})

    result = agent.process(ctx, signal)

    assert len(result["new_skill_bundles"]) == 0
    assert "_reason" in result


def test_architect_output_schema():
    """Architect output matches ARCHITECT_SPEC.output_schema."""
    from src.scoring.agents import ArchitectAgent
    from src.scoring.policy import ScoringContext, ScoringSignal

    agent = ArchitectAgent(min_nodes_per_profile=2)

    ctx = ScoringContext(
        nodes=[
            {"id": "n1", "content": "debug k8s pod", "outcome": "success"},
            {"id": "n2", "content": "kubectl logs", "outcome": "success"},
        ],
        domain="k8s",
    )
    signal = ScoringSignal(trigger=True, score=0.7, confidence=0.8, breakdown={}, reason="test", metadata={})

    result = agent.process(ctx, signal)

    assert "new_profiles" in result
    assert "updated_profiles" in result
    assert "retired_profiles" in result


def test_architect_insufficient_nodes():
    """Architect returns empty when not enough nodes."""
    from src.scoring.agents import ArchitectAgent
    from src.scoring.policy import ScoringContext, ScoringSignal

    agent = ArchitectAgent(min_nodes_per_profile=5)

    ctx = ScoringContext(
        nodes=[{"id": "n1", "content": "test"}],
        domain="debug",
    )
    signal = ScoringSignal(trigger=True, score=0.5, confidence=0.6, breakdown={}, reason="test", metadata={})

    result = agent.process(ctx, signal)

    assert len(result["new_profiles"]) == 0
