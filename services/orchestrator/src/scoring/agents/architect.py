"""Architect agent — compile experience nodes into BaseProfile bundles."""
from __future__ import annotations
import uuid
from typing import Any

from src.scoring.taskspec import ARCHITECT_SPEC
from src.scoring.policy import ScoringContext, ScoringSignal
from src.agent.profile import AgentBaseProfile, LayerProfile


class ArchitectAgent:
    """
    Compiles domain experience nodes into BaseProfile bundles.

    Role: architect
    Negotiation: vote
    LLM role: explain (generates L1/L2 Layer content)
    """

    def __init__(
        self,
        profile_registry: Any | None = None,
        llm_client: Any | None = None,
        min_nodes_per_profile: int = 5,
    ) -> None:
        self._registry = profile_registry
        self._llm = llm_client
        self._spec = ARCHITECT_SPEC
        self._min_nodes = min_nodes_per_profile

    def process(
        self,
        context: ScoringContext,
        signal: ScoringSignal | None,
    ) -> dict[str, Any]:
        """
        Process experience nodes and compile BaseProfile bundles.

        Returns dict matching ARCHITECT_SPEC.output_schema:
        {
            "new_profiles": [...],
            "updated_profiles": [],
            "retired_profiles": []
        }
        """
        nodes = context.nodes or []
        domain = context.domain or "general"

        if len(nodes) < self._min_nodes:
            return {
                "new_profiles": [],
                "updated_profiles": [],
                "retired_profiles": [],
                "_reason": f"not enough nodes ({len(nodes)} < {self._min_nodes})",
            }

        # Compile into AgentBaseProfile
        profile = self._compile_profile(nodes, domain, signal)

        # Register if registry available
        if self._registry:
            try:
                self._registry.register(profile)
            except Exception:
                pass

        return {
            "new_profiles": [self._profile_to_dict(profile)],
            "updated_profiles": [],
            "retired_profiles": [],
        }

    def _compile_profile(
        self,
        nodes: list[dict],
        domain: str,
        signal: ScoringSignal | None,
    ) -> AgentBaseProfile:
        """Compile experience nodes into AgentBaseProfile."""
        profile = AgentBaseProfile(
            agent_id=f"auto_{domain}_{uuid.uuid4().hex[:8]}"
        )

        # L1: Agent Identity — domain patterns + intent
        l1_content = self._compile_l1(nodes)
        profile.add_layer(LayerProfile(
            layer=1,
            source="experience_l2",
            content=l1_content,
            priority=10,
            tags=["auto_generated", domain],
        ))

        # L2: Operational Guidelines — failure/recovery patterns
        l2_content = self._compile_l2(nodes)
        profile.add_layer(LayerProfile(
            layer=2,
            source="experience_l2",
            content=l2_content,
            priority=9,
            tags=["auto_generated", domain],
        ))

        # L3: Contextual Memory — domain knowledge
        l3_content = self._compile_l3(nodes, domain)
        profile.add_layer(LayerProfile(
            layer=3,
            source="experience_l2",
            content=l3_content,
            priority=8,
            tags=["auto_generated", domain],
        ))

        # L4: Tool definitions — tool call patterns
        l4_content = self._compile_l4(nodes)
        profile.add_layer(LayerProfile(
            layer=4,
            source="experience_l2",
            content=l4_content,
            priority=7,
            tags=["auto_generated", domain],
        ))

        return profile

    def _compile_l1(self, nodes: list[dict]) -> str:
        """Compile L1: Agent Identity from intent patterns."""
        intents = set()
        for n in nodes:
            content = n.get("content", "")
            if "intent" in n:
                intents.add(n["intent"])
            elif content:
                intents.add(content[:100])

        if len(intents) > 5:
            intents = list(intents)[:5]

        return f"Domain expertise: {', '.join(sorted(intents))}"

    def _compile_l2(self, nodes: list[dict]) -> str:
        """Compile L2: Operational Guidelines from failure patterns."""
        failures = []
        for n in nodes:
            content = n.get("content", "")
            outcome = n.get("outcome", "")
            if outcome == "failure" or "error" in content.lower():
                failures.append(content[:150])

        if failures:
            return "Failure patterns to avoid:\n" + "\n".join(f"- {f}" for f in failures[:5])
        return "Operational guidelines from experience."

    def _compile_l3(self, nodes: list[dict], domain: str) -> str:
        """Compile L3: Contextual Memory from domain knowledge."""
        contents = [n.get("content", "")[:200] for n in nodes[:3] if n.get("content")]
        if not contents:
            return f"Domain: {domain}"
        return f"Key knowledge:\n" + "\n".join(f"- {c}" for c in contents)

    def _compile_l4(self, nodes: list[dict]) -> str:
        """Compile L4: Tool definitions from tool call patterns."""
        tools = set()
        for n in nodes:
            for tc in n.get("tool_calls", []):
                tools.add(tc.get("name", ""))
        if tools:
            return f"Relevant tools: {', '.join(sorted(tools))}"
        return ""

    def _profile_to_dict(self, profile: AgentBaseProfile) -> dict:
        """Serialize AgentBaseProfile for output."""
        return {
            "agent_id": profile.agent_id,
            "name": f"auto_{profile.agent_id}",
            "domain": "general",
            "layer_count": sum(len(v) for v in profile.layers.values()),
            "sources": profile.get_all_sources(),
        }
