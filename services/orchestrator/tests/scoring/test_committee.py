"""Tests for ProfileGenerationCommittee."""
import pytest
from unittest.mock import MagicMock
from src.scoring.policy import ScoringContext, ScoringSignal
from src.scoring.engine import ScoringEngine
from src.scoring.taskspec import TRANSCRIBER_SPEC, REFINER_SPEC
from src.scoring.committee import ProfileGenerationCommittee, CommitteeResult


def dummy_transcriptor(ctx, signal):
    return {
        "relevant_facts": [{"id": "f1", "content": "test"}],
        "discarded_facts": [],
        "extraction_metadata": {"total_action_units": 1, "relevant_count": 1, "discarded_count": 0},
    }


def dummy_refiner(ctx, signal):
    return {"new_skill_bundles": [], "retired_bundles": [], "merge_decisions": []}


class DummyPolicy:
    """Dummy policy that always triggers with given signal."""
    def __init__(self, trigger: bool, score: float):
        self.name = "dummy"
        self._trigger = trigger
        self._score = score

    def evaluate(self, ctx):
        return ScoringSignal(
            trigger=self._trigger,
            score=self._score,
            confidence=0.9,
            breakdown={},
            reason="dummy",
            metadata={"policy": "dummy"}
        )

    def get_threshold(self):
        return 0.5


def test_committee_no_trigger():
    """Committee should not run if scoring signal doesn't trigger."""
    engine = ScoringEngine([DummyPolicy(trigger=False, score=0.2)], combine_mode="any")
    committee = ProfileGenerationCommittee(
        scoring_engine=engine,
        transcriptor_fn=dummy_transcriptor,
    )
    ctx = ScoringContext(nodes=[{"id": "n1"}])

    result = committee.process(ctx)

    assert result.triggered is False
    assert result.approved is False
    assert result.outputs == {}


def test_committee_transcriptor_runs():
    """Transcriber should always run when triggered."""
    engine = ScoringEngine([DummyPolicy(trigger=True, score=0.6)], combine_mode="any")
    committee = ProfileGenerationCommittee(
        scoring_engine=engine,
        transcriptor_fn=dummy_transcriptor,
    )
    ctx = ScoringContext(nodes=[{"id": "n1"}])

    result = committee.process(ctx)

    assert result.triggered is True
    assert "transcriber" in result.outputs
    assert result.outputs["transcriber"]["relevant_facts"][0]["id"] == "f1"


def test_committee_refiner_skipped_low_score():
    """Refiner should skip if score < 0.5."""
    engine = ScoringEngine([DummyPolicy(trigger=True, score=0.4)], combine_mode="any")
    committee = ProfileGenerationCommittee(
        scoring_engine=engine,
        transcriptor_fn=dummy_transcriptor,
        refiner_fn=dummy_refiner,
    )
    ctx = ScoringContext(nodes=[{"id": "n1"}])

    result = committee.process(ctx)

    assert result.triggered is True
    assert "refiner" not in result.outputs  # Skipped due to low score


def test_committee_vote_consensus():
    """Committee approved when all vote roles agree."""
    engine = ScoringEngine([DummyPolicy(trigger=True, score=0.7)], combine_mode="any")
    committee = ProfileGenerationCommittee(
        scoring_engine=engine,
        transcriptor_fn=dummy_transcriptor,
        refiner_fn=dummy_refiner,
    )
    ctx = ScoringContext(nodes=[{"id": "n1"}])

    result = committee.process(ctx)

    assert result.triggered is True
    assert result.approved is True
    assert result.votes.get("refiner") is True


def test_committee_last_signal_recorded():
    """Last scoring signal should be recorded."""
    engine = ScoringEngine([DummyPolicy(trigger=True, score=0.8)], combine_mode="any")
    committee = ProfileGenerationCommittee(scoring_engine=engine)
    ctx = ScoringContext(nodes=[{"id": "n1"}])

    committee.process(ctx)

    assert committee.last_signal is not None
    assert committee.last_signal.score == 0.8