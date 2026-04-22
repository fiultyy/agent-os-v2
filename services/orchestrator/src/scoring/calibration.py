"""ScoringCalibrationSystem — threshold self-convergence via offline backtesting."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any

from .policy import ScoringPolicy, ScoringContext


@dataclass
class CalibrationResult:
    """Result of one calibration run."""
    forward_threshold: float
    backward_threshold: float
    co_min: int
    precision: float
    recall: float
    f1: float
    tp: int
    fp: int
    fn: int


class BundleHistory:
    """
    Tracks bundle usage for ground truth in calibration.
    
    Call record_call() whenever an agent uses a bundle.
    was_called() returns whether a bundle was used in subsequent sessions.
    """
    
    def __init__(self) -> None:
        self._calls: dict[tuple[str, str], str] = {}  # (bundle_id, session_id) -> timestamp
    
    def record_call(self, bundle_id: str, session_id: str) -> None:
        from datetime import datetime, timezone
        self._calls[(bundle_id, session_id)] = datetime.now(timezone.utc).isoformat()
    
    def was_called(self, bundle_id: str) -> bool:
        return any(k[0] == bundle_id for k in self._calls)
    
    def get_all_bundles(self) -> set[str]:
        return {k[0] for k in self._calls}


class ScoringCalibrationSystem:
    """
    Side-line system for threshold self-convergence.
    
    Not blocking to main flow. Runs periodically to tune thresholds.
    """
    
    def __init__(
        self,
        kg: Any,  # KnowledgeGraph
        scoring_policy: ScoringPolicy,
        bundle_history: BundleHistory,
    ) -> None:
        self._kg = kg
        self._policy = scoring_policy
        self._history = bundle_history
    
    def run(self) -> CalibrationResult | None:
        """
        Run one calibration cycle.
        
        1. Collect historical KG data
        2. Enumerate threshold grid
        3. Backtest each combination
        4. Compute F1 scores
        5. Update policy thresholds
        
        Returns None if no data available.
        """
        # TODO: Full implementation with offline backtesting
        # For now, return None (placeholder)
        return None
