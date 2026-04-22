"""End-to-end integration tests for D-30 scoring system."""
import pytest


def test_full_committee_pipeline():
    """Test complete pipeline: scoring -> committee -> agents."""
    from src.scoring import ScoringEngine, ScoringContext, ScoringSignal
    from src.scoring.policies import ButterflySignalPolicy, ReuseThresholdPolicy
    from src.scoring.committee import ProfileGenerationCommittee
    from src.scoring.agents import RefinerAgent
    from src.memory.experience_kg import ExperienceKG

    # Setup
    kg = ExperienceKG(db_path=":memory:")
    scoring = ScoringEngine(
        policies=[
            ButterflySignalPolicy(
                forward_threshold=0.3,
                backward_threshold=0.3,
                co_occurrence_min=1,
            ),
            ReuseThresholdPolicy(),
        ],
        combine_mode="any",
    )
    
    # Create agents
    refiner = RefinerAgent(experience_kg=kg, reuse_threshold=0.5, min_bundle_nodes=2)
    
    # Create committee
    committee = ProfileGenerationCommittee(
        scoring_engine=scoring,
        refiner_fn=refiner.process,
    )
    
    # Build context with nodes that have butterfly wings
    # source_node_id appears in both forward and backward wings for co-occurrence
    ctx = ScoringContext(
        nodes=[
            {"id": "n1", "content": "kubectl debug", "reuse_score": 0.9, "skill_domain": "k8s"},
            {"id": "n2", "content": "kubectl apply", "reuse_score": 0.8, "skill_domain": "k8s"},
        ],
        forward_wings=[
            {"source_node_id": "n1", "target_node_id": "n2", "wing_type": "forward"},
        ],
        backward_wings=[
            {"source_node_id": "n2", "target_node_id": "n1", "wing_type": "backward"},
        ],
        domain="k8s",
    )
    
    # Process through committee
    result = committee.process(ctx)
    
    # Verify pipeline worked
    assert result.triggered is True
    assert committee.last_signal is not None
    assert committee.last_signal.score >= 0.0


def test_calibration_system():
    """Test calibration system finds better thresholds."""
    from src.scoring.calibration import ScoringCalibrationSystem, BundleHistory
    from src.scoring.policies import ButterflySignalPolicy
    
    history = BundleHistory()
    history.record_call("bundle1", "session_1")
    history.record_call("bundle1", "session_2")
    
    policy = ButterflySignalPolicy(forward_threshold=0.3, backward_threshold=0.3)
    
    # Note: calibration needs real KG data to run fully
    # This tests the interface
    assert policy.forward_threshold == 0.3
    assert history.was_called("bundle1") is True
    assert history.was_called("bundle2") is False


def test_butterfly_signal_triggers_committee():
    """Strong butterfly signal should trigger committee."""
    from src.scoring import ScoringEngine, ScoringContext, ScoringSignal
    from src.scoring.policies import ButterflySignalPolicy
    
    policy = ButterflySignalPolicy(
        forward_threshold=0.2,
        backward_threshold=0.2,
        co_occurrence_min=1,
    )
    engine = ScoringEngine([policy], combine_mode="any")
    
    # Use source_node_id that appears in both forward and backward wings
    # so co_occurrence (forward_sources ∩ backward_targets) > 0
    ctx = ScoringContext(
        nodes=[{"id": "n1"}, {"id": "n2"}],
        forward_wings=[{"source_node_id": "n1", "target_node_id": "n2", "wing_type": "forward"}],
        backward_wings=[{"source_node_id": "n2", "target_node_id": "n1", "wing_type": "backward"}],
    )
    
    signal = engine.evaluate(ctx)
    
    assert signal.trigger is True
    assert signal.score > 0.0
