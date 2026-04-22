"""Refiner agent — refine experience nodes into skill bundles."""
from __future__ import annotations
import re
from typing import Any

from src.scoring.taskspec import REFINER_SPEC
from src.scoring.policy import ScoringContext, ScoringSignal
from src.memory.experience_kg import ExperienceKG


class RefinerAgent:
    """
    Refines high-reuse experience nodes into skill bundles.

    Role: refiner
    Negotiation: vote
    LLM role: explain (generates description)
    """

    def __init__(
        self,
        experience_kg: ExperienceKG,
        llm_client: Any | None = None,
        reuse_threshold: float = 0.7,
        min_bundle_nodes: int = 3,
    ) -> None:
        self._kg = experience_kg
        self._llm = llm_client
        self._spec = REFINER_SPEC
        self._reuse_threshold = reuse_threshold
        self._min_bundle_nodes = min_bundle_nodes

    def process(
        self,
        context: ScoringContext,
        signal: ScoringSignal | None,
    ) -> dict[str, Any]:
        """
        Process experience nodes and create skill bundles.

        Returns dict matching REFINER_SPEC.output_schema:
        {
            "new_skill_bundles": [...],
            "retired_bundles": [],
            "merge_decisions": []
        }
        """
        nodes = context.nodes or []

        # Filter high-reuse nodes
        high_reuse = [
            n for n in nodes
            if float(n.get("reuse_score", 0.0)) >= self._reuse_threshold
        ]

        if len(high_reuse) < self._min_bundle_nodes:
            return {
                "new_skill_bundles": [],
                "retired_bundles": [],
                "merge_decisions": [],
                "_reason": f"not enough high-reuse nodes ({len(high_reuse)} < {self._min_bundle_nodes})",
            }

        # Group by domain
        by_domain: dict[str, list[dict]] = {}
        for node in high_reuse:
            domain = node.get("skill_domain", "general") or "general"
            if domain not in by_domain:
                by_domain[domain] = []
            by_domain[domain].append(node)

        new_bundles = []
        for domain, domain_nodes in by_domain.items():
            if len(domain_nodes) < self._min_bundle_nodes:
                continue

            # Generate name and description
            name = self._generate_skill_name(domain, domain_nodes)
            description = self._generate_description(domain_nodes)

            node_ids = [n.get("id", n.get("memory_id", "")) for n in domain_nodes]
            node_ids = [nid for nid in node_ids if nid]

            try:
                bundle_id = self._kg.create_skill_bundle(
                    name=name,
                    node_ids=node_ids,
                    domain=domain,
                    description=description,
                )
                new_bundles.append({
                    "bundle_id": bundle_id,
                    "name": name,
                    "domain": domain,
                    "node_count": len(node_ids),
                    "description": description,
                })
            except Exception:
                pass

        return {
            "new_skill_bundles": new_bundles,
            "retired_bundles": [],
            "merge_decisions": [],
        }

    def _generate_skill_name(self, domain: str, nodes: list[dict]) -> str:
        """Generate skill name from domain and node content."""
        tool_names = set()
        for n in nodes:
            content = n.get("content", "")
            tools = re.findall(r'`(\w+)`', content)
            tool_names.update(tools[:3])

        if tool_names:
            tools_str = "_".join(sorted(tool_names)[:2])
            return f"{domain}_{tools_str}"
        return f"{domain}_skill_bundle"

    def _generate_description(self, nodes: list[dict]) -> str:
        """Generate description using LLM or heuristic."""
        if self._llm and hasattr(self._llm, "agenerate"):
            try:
                contents = [n.get("content", "")[:200] for n in nodes[:5]]
                prompt = (
                    f"Generate a one-sentence description for a skill bundle "
                    f"containing these experiences:\n" + "\n".join(contents)
                )
                response = self._llm.agenerate(prompt)
                return response.strip()[:200] if response else f"Skill bundle with {len(nodes)} experiences"
            except Exception:
                pass

        # Fallback: heuristic
        return f"Skill bundle with {len(nodes)} high-reuse experience nodes"
