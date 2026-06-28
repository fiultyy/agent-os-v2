"""ButterflySignalPolicy — butterfly wing + KG graph structure scoring."""
from __future__ import annotations
from dataclasses import dataclass

from ..policy import ScoringContext, ScoringSignal


@dataclass
class ButterflySignalPolicy:
    """
    Butterfly signal scoring policy.
    
    Signal = forward_strength × backward_strength × co_occurrence_rate × structural_weight
    
    KG graph structure features provide objective quantization:
    - out_degree: how many entities this one points to
    - in_degree: how many entities point to this one
    - pagerank: global importance
    - cluster_coef: clustering coefficient (density of local subgraph)
    """
    
    name: str = "butterfly_signal"
    forward_threshold: float = 0.6
    backward_threshold: float = 0.6
    co_occurrence_min: int = 3
    structural_weight_enable: bool = True
    
    def evaluate(self, context: ScoringContext) -> ScoringSignal:
        # Calculate forward strength
        forward_strength = self._calc_forward_strength(
            context.forward_wings, context.nodes
        )
        
        # Calculate backward strength
        backward_strength = self._calc_backward_strength(
            context.backward_wings, context.nodes
        )
        
        # Co-occurrence rate
        co_occurrence = self._calc_co_occurrence(
            context.forward_wings, context.backward_wings
        )
        co_rate = min(co_occurrence / max(self.co_occurrence_min, 1), 1.0)
        
        # Butterfly core signal
        butterfly_core = forward_strength * backward_strength * co_rate
        
        # Structural weight from KG graph structure
        if self.structural_weight_enable and context.graph_stats:
            structural = self._calc_structural_weight(context.graph_stats)
        else:
            structural = 1.0
        
        signal_score = butterfly_core * structural
        
        trigger = (
            forward_strength >= self.forward_threshold and
            backward_strength >= self.backward_threshold
        )
        
        return ScoringSignal(
            trigger=trigger,
            score=signal_score,
            confidence=(forward_strength + backward_strength) / 2,
            breakdown={
                "forward_strength": forward_strength,
                "backward_strength": backward_strength,
                "co_occurrence_rate": co_rate,
                "structural_weight": structural if self.structural_weight_enable else 1.0,
                "butterfly_core": butterfly_core,
            },
            reason=f"butterfly: fwd={forward_strength:.2f}, bwd={backward_strength:.2f}, struct={structural:.2f}",
            metadata={"policy": self.name}
        )
    
    def get_threshold(self) -> float:
        return self.forward_threshold  # Return primary threshold
    
    def _calc_forward_strength(
        self, wings: list[dict], nodes: list[dict]
    ) -> float:
        if not wings or not nodes:
            return 0.0
        # Forward strength = fraction of nodes with forward wings
        winged_nodes = {w.get("source_node_id") or w.get("source") for w in wings}
        return min(len(winged_nodes) / max(len(nodes), 1), 1.0)
    
    def _calc_backward_strength(
        self, wings: list[dict], nodes: list[dict]
    ) -> float:
        if not wings or not nodes:
            return 0.0
        # Backward strength = fraction of nodes targeted by backward wings
        winged_nodes = {w.get("target_node_id") or w.get("target") for w in wings}
        return min(len(winged_nodes) / max(len(nodes), 1), 1.0)
    
    def _calc_co_occurrence(
        self, forward_wings: list[dict], backward_wings: list[dict]
    ) -> int:
        # Count shared nodes between forward and backward wings
        forward_sources = {w.get("source_node_id") or w.get("source") for w in forward_wings}
        backward_targets = {w.get("target_node_id") or w.get("target") for w in backward_wings}
        return len(forward_sources & backward_targets)
    
    def _calc_structural_weight(self, graph_stats: dict) -> float:
        """
        Calculate structural weight from KG graph statistics.
        
        Formula: normalized(out_degree) + normalized(in_degree) * 1.5 + pagerank * 2 + cluster_coef
        """
        out_d = float(graph_stats.get("out_degree", 0))
        in_d = float(graph_stats.get("in_degree", 0))
        pr = float(graph_stats.get("pagerank", 0.0))
        cc = float(graph_stats.get("cluster_coef", 0.0))
        
        # Normalize (assume max_out = 20, max_in = 20)
        norm_out = min(out_d / 20.0, 1.0)
        norm_in = min(in_d / 20.0, 1.0)
        
        structural = (norm_out + norm_in * 1.5 + pr * 2.0 + cc) / 5.0
        return min(max(structural, 0.1), 2.0)  # Clamp to [0.1, 2.0]
