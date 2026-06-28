"""ProfileGenerationCommittee — three-role negotiation for profile generation.

NOT-WIRED (deferred): committee 可观测/调参 API(get_spec/last_result/last_signal)
+ engine.set_weights/add_policy 仅 test 引用,生产未消费。属记忆评分红线(蝴蝶翼/
ExperienceKG 写侧 committee)的接口面,保留以便观测/调参接线。
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Callable

from .policy import ScoringContext, ScoringSignal
from .engine import ScoringEngine
from .taskspec import TaskSpec, TRANSCRIBER_SPEC, REFINER_SPEC, ARCHITECT_SPEC


@dataclass
class CommitteeResult:
    """Result of one Committee negotiation cycle."""
    triggered: bool
    signal: ScoringSignal | None
    outputs: dict[str, dict[str, Any]]
    votes: dict[str, bool]  # role -> approved
    approved: bool
    reason: str


class ProfileGenerationCommittee:
    """
    Profile Generation Committee.

    Three roles (Transcriber, Refiner, Architect) negotiate via JSON
    to decide whether to commit skill/BaseProfile bundles.

    Negotiation flow:
    1. ScoringEngine evaluates -> ScoringSignal
    2. If trigger, execute each role
    3. Roles with "vote" negotiation collect votes
    4. If consensus, commit outputs
    """

    def __init__(
        self,
        scoring_engine: ScoringEngine,
        transcriptor_fn: Callable[[ScoringContext, ScoringSignal], dict] | None = None,
        refiner_fn: Callable[[ScoringContext, ScoringSignal], dict] | None = None,
        architect_fn: Callable[[ScoringContext, ScoringSignal], dict] | None = None,
    ) -> None:
        self._scoring = scoring_engine
        self._fns = {
            "transcriber": transcriptor_fn,
            "refiner": refiner_fn,
            "architect": architect_fn,
        }
        self._specs = {
            "transcriber": TRANSCRIBER_SPEC,
            "refiner": REFINER_SPEC,
            "architect": ARCHITECT_SPEC,
        }
        # Last run stats
        self._last_signal: ScoringSignal | None = None
        self._last_result: CommitteeResult | None = None

    def get_spec(self, role: str) -> TaskSpec:
        """Get TaskSpec for a role."""
        return self._specs.get(role)

    def process(self, context: ScoringContext) -> CommitteeResult:
        """
        Execute one Committee negotiation cycle.

        Args:
            context: ScoringContext with nodes, wings, graph_stats

        Returns:
            CommitteeResult with trigger decision and outputs
        """
        # Step 1: Evaluate scoring signal
        signal = self._scoring.evaluate(context)
        self._last_signal = signal

        if not signal.trigger:
            self._last_result = CommitteeResult(
                triggered=False,
                signal=signal,
                outputs={},
                votes={},
                approved=False,
                reason=f"scoring_signal.trigger=False, score={signal.score:.3f}",
            )
            return self._last_result

        # Step 2: Execute each role (transcriber always runs)
        outputs: dict[str, dict[str, Any]] = {}

        # Transcriber: always executes
        transcriber_fn = self._fns.get("transcriber")
        if transcriber_fn:
            try:
                outputs["transcriber"] = transcriber_fn(context, signal)
            except Exception as e:
                outputs["transcriber"] = {"error": str(e)}

        # Refiner and Architect: only if scoring is strong
        for role in ["refiner", "architect"]:
            fn = self._fns.get(role)
            spec = self._specs[role]
            if fn and signal.score >= 0.5:  # Only run if strong signal
                try:
                    outputs[role] = fn(context, signal)
                except Exception as e:
                    outputs[role] = {"error": str(e)}

        # Step 3: Vote (for roles with negotiation="vote")
        votes: dict[str, bool] = {}
        for role in ["refiner", "architect"]:
            spec = self._specs[role]
            if spec.negotiation == "vote" and role in outputs:
                # Vote = True if output is non-empty and no error
                output = outputs[role]
                votes[role] = (
                    isinstance(output, dict)
                    and "error" not in output
                    and len(output) > 0
                )

        # Step 4: Consensus check
        if votes:
            approved = all(votes.values())
        else:
            approved = True  # No vote needed

        self._last_result = CommitteeResult(
            triggered=True,
            signal=signal,
            outputs=outputs if approved else {},
            votes=votes,
            approved=approved,
            reason=f"approved={approved}, votes={votes}",
        )
        return self._last_result

    @property
    def last_result(self) -> CommitteeResult | None:
        return self._last_result

    @property
    def last_signal(self) -> ScoringSignal | None:
        return self._last_signal