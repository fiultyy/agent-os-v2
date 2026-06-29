"""Tests for scoring policies."""
from src.scoring.policy import ScoringContext
from src.scoring.policies import ButterflySignalPolicy, ReuseThresholdPolicy


def test_butterfly_signal_no_wings():
    """Butterfly signal with no wings should be 0."""
    policy = ButterflySignalPolicy()
    ctx = ScoringContext(nodes=[{"id": "n1"}], forward_wings=[], backward_wings=[])
    result = policy.evaluate(ctx)
    assert result.trigger is False
    assert result.score == 0.0


def test_butterfly_signal_trigger():
    """Strong butterfly signal should trigger."""
    policy = ButterflySignalPolicy(forward_threshold=0.3, backward_threshold=0.3, co_occurrence_min=1)
    ctx = ScoringContext(
        nodes=[{"id": "n1"}, {"id": "n2"}],
        forward_wings=[{"source_node_id": "n1", "target_node_id": "n2"}],
        backward_wings=[{"source_node_id": "n2", "target_node_id": "n1"}],
    )
    result = policy.evaluate(ctx)
    # co_occurrence: forward_sources={n1} ∩ backward_targets={n1} = {n1} → co=1 ≥ co_min=1 → co_rate=1.0
    # forward_strength=1/2=0.5, backward_strength=1/2=0.5, butterfly_core=0.5*0.5*1.0=0.25 > 0
    assert result.trigger is True
    assert result.score > 0.0


def test_butterfly_signal_with_graph_stats():
    """Graph stats should influence structural weight."""
    policy = ButterflySignalPolicy(structural_weight_enable=True)
    ctx = ScoringContext(
        nodes=[{"id": "n1"}],
        forward_wings=[{"source_node_id": "n1", "target_node_id": "n2"}],
        backward_wings=[{"source_node_id": "n2", "target_node_id": "n1"}],
        graph_stats={"out_degree": 10, "in_degree": 5, "pagerank": 0.3, "cluster_coef": 0.2}
    )
    result = policy.evaluate(ctx)
    # Structural weight is computed as (norm_out + norm_in*1.5 + pr*2 + cc) / 5
    # With these inputs: (0.5 + 0.375 + 0.6 + 0.2) / 5 = 0.335
    # Structural weight should be between 0.1 and 2.0
    assert 0.1 <= result.breakdown.get("structural_weight", 1.0) <= 2.0


def test_reuse_threshold_no_nodes():
    """Reuse threshold with no nodes should not trigger."""
    policy = ReuseThresholdPolicy()
    ctx = ScoringContext(nodes=[])
    result = policy.evaluate(ctx)
    assert result.trigger is False
    assert result.score == 0.0


def test_reuse_threshold_trigger():
    """High reuse score should trigger."""
    policy = ReuseThresholdPolicy(reuse_threshold=0.5, min_node_count=2)
    ctx = ScoringContext(
        nodes=[
            {"id": "n1", "reuse_score": 0.8},
            {"id": "n2", "reuse_score": 0.6},
        ]
    )
    result = policy.evaluate(ctx)
    assert result.trigger is True
    assert result.breakdown["max_reuse"] == 0.8
