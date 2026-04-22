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
        # Collect historical nodes and wings from KG
        try:
            nodes = self._kg.get_all_experience_nodes()
            wings = self._kg.get_all_wings()
        except Exception:
            return None
        
        if not nodes or len(nodes) < 5:
            return None
        
        best_f1 = 0.0
        best_params: dict[str, Any] = {}
        
        # Grid search over thresholds
        for fwd_t in [0.3, 0.5, 0.6, 0.7]:
            for bwd_t in [0.3, 0.5, 0.6, 0.7]:
                for co_min in [2, 3, 5]:
                    # Simulate trigger with this threshold
                    triggered = self._simulate_trigger(
                        nodes, wings,
                        forward_threshold=fwd_t,
                        backward_threshold=bwd_t,
                        co_min=co_min
                    )
                    
                    # Compute TP/FP/FN using bundle history
                    tp = fp = fn = 0
                    for bundle_id in triggered:
                        if self._history.was_called(bundle_id):
                            tp += 1
                        else:
                            fp += 1
                    
                    for bundle_id in self._history.get_all_bundles():
                        if bundle_id not in triggered and self._history.was_called(bundle_id):
                            fn += 1
                    
                    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
                    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
                    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
                    
                    if f1 > best_f1:
                        best_f1 = f1
                        best_params = {
                            "forward_threshold": fwd_t,
                            "backward_threshold": bwd_t,
                            "co_min": co_min,
                            "precision": precision,
                            "recall": recall,
                            "f1": f1,
                            "tp": tp, "fp": fp, "fn": fn,
                        }
        
        if not best_params:
            return None
        
        # Update policy thresholds if we found a better combination
        self._policy.forward_threshold = best_params["forward_threshold"]
        self._policy.backward_threshold = best_params["backward_threshold"]
        if hasattr(self._policy, "co_occurrence_min"):
            self._policy.co_occurrence_min = best_params["co_min"]
        
        return CalibrationResult(**best_params)
    
    def _simulate_trigger(
        self,
        nodes: list[dict],
        wings: list[dict],
        forward_threshold: float,
        backward_threshold: float,
        co_min: int,
    ) -> set[str]:
        """Simulate which bundles would be triggered with given thresholds."""
        triggered = set()
        
        # Group wings by node
        node_wings: dict[str, list] = {}
        for w in wings:
            src = w.get("source_node_id", "")
            tgt = w.get("target_node_id", "")
            if src:
                node_wings.setdefault(src, []).append(w)
            if tgt:
                node_wings.setdefault(tgt, []).append(w)
        
        for node in nodes:
            nid = node.get("id", "")
            n_wings = node_wings.get(nid, [])
            
            forward_count = sum(1 for w in n_wings if w.get("wing_type") in ("forward", "bidirectional"))
            backward_count = sum(1 for w in n_wings if w.get("wing_type") in ("backward", "bidirectional"))
            
            # Simple thresholds
            if forward_count >= 1 and backward_count >= 1:
                triggered.add(nid)
        
        return triggered
