"""Experience Tool — interactive experience KG tool for agents.

Provides structured access to workspace experiences:
- get_top_experiences: high-reuse experience nodes
- get_butterfly_associations: butterfly wing associations
- create_skill: bundle experiences into a reusable skill
- list_skills: available skill bundles
- summarize_experience: generate experience summaries
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from src.memory.experience_kg import ExperienceKG
from src.memory.sideline.reuse_tracker import ReuseTracker


class ExperienceTool:
    """Experience tool for agents to interact with the experience KG.

    Operations:
    - get_top_experiences: return top N high-reuse experience nodes
    - get_butterfly_associations: get butterfly wing associations for an entity
    - create_skill: create a skill bundle from experience nodes
    - list_skills: list available skill bundles
    - summarize_experience: generate a summary from experience nodes
    """

    def __init__(
        self,
        experience_kg: ExperienceKG,
        reuse_tracker: ReuseTracker | None = None,
    ) -> None:
        self._exp_kg = experience_kg
        self._reuse = reuse_tracker
        self._executor = ThreadPoolExecutor(max_workers=2)

    def execute(self, operation: str, params: dict[str, Any]) -> dict[str, Any]:
        """Synchronous execute wrapper (for sync tool registry)."""
        try:
            loop = asyncio.get_running_loop()
            return loop.run_until_complete(self._execute_async(operation, params))
        except RuntimeError:
            # No running event loop
            return asyncio.run(self._execute_async(operation, params))

    async def _execute_async(
        self, operation: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        """Async execute implementation."""
        if operation == "get_top_experiences":
            return self._get_top_experiences(params)
        elif operation == "get_butterfly_associations":
            return self._get_butterfly_associations(params)
        elif operation == "create_skill":
            return self._create_skill(params)
        elif operation == "list_skills":
            return self._list_skills(params)
        elif operation == "summarize_experience":
            return await self._summarize_experience(params)
        else:
            raise ValueError(f"Unknown operation: {operation}")

    def _get_top_experiences(self, params: dict) -> dict[str, Any]:
        """Return top N high-reuse experience nodes."""
        limit = params.get("limit", 10)
        domain = params.get("domain")
        nodes = self._exp_kg.get_top_experience_nodes(domain=domain, limit=limit)
        return {
            "experiences": nodes,
            "count": len(nodes),
        }

    def _get_butterfly_associations(self, params: dict) -> dict[str, Any]:
        """Get butterfly associations for an entity/node."""
        entity_id = params.get("entity_id")
        if not entity_id:
            return {"error": "entity_id is required"}
        result = self._exp_kg.build_butterfly_associations(entity_id)
        return result

    def _create_skill(self, params: dict) -> dict[str, Any]:
        """Create a skill bundle from experience nodes."""
        name = params.get("name")
        node_ids = params.get("node_ids", [])
        domain = params.get("domain", "")
        description = params.get("description", "")

        if not name:
            return {"error": "name is required"}
        if not node_ids:
            return {"error": "node_ids is required (list of entity IDs)"}

        bundle_id = self._exp_kg.create_skill_bundle(
            name=name,
            node_ids=node_ids,
            domain=domain,
            description=description,
        )
        return {
            "bundle_id": bundle_id,
            "name": name,
            "node_count": len(node_ids),
        }

    def _list_skills(self, params: dict) -> dict[str, Any]:
        """List available skill bundles."""
        domain = params.get("domain")
        bundles = self._exp_kg.get_skill_bundles(domain=domain)
        return {
            "skills": bundles,
            "count": len(bundles),
        }

    async def _summarize_experience(self, params: dict) -> dict[str, Any]:
        """Summarize experience nodes into a readable summary.

        Currently returns structured data. LLM summarization
        can be added via the _llm_summarize method.
        """
        node_ids = params.get("node_ids", [])
        format_type = params.get("format", "text")

        nodes = []
        for node_id in node_ids:
            node = self._exp_kg.get_experience_node(node_id)
            if node:
                nodes.append(node)

        if not nodes:
            return {"error": "No valid experience nodes found"}

        if format_type == "text":
            summary_lines = [
                f"## {node.get('skill_domain', 'general')}: {node.get('content', '')[:200]}"
                + (f" (score: {node.get('reuse_score', 0):.1f})" if node.get('reuse_score') else "")
                for node in nodes
            ]
            return {
                "summary": "\n\n".join(summary_lines),
                "node_count": len(nodes),
            }
        else:
            return {
                "experiences": nodes,
                "node_count": len(nodes),
            }

    @staticmethod
    def get_tool_definition() -> dict[str, Any]:
        """Return the LLM tool definition for this experience tool."""
        return {
            "name": "experience_tool",
            "description": (
                "Query and organize workspace experiences. Use to find high-reuse skills, "
                "explore butterfly associations between experiences, create skill bundles "
                "from multiple experience nodes, list available skills, or summarize "
                "experience content for sharing."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "operation": {
                        "type": "string",
                        "enum": [
                            "get_top_experiences",
                            "get_butterfly_associations",
                            "create_skill",
                            "list_skills",
                            "summarize_experience",
                        ],
                        "description": "The experience operation to perform",
                    },
                    "params": {
                        "type": "object",
                        "description": (
                            "Operation parameters:\n"
                            "- get_top_experiences: {limit: int, domain?: str}\n"
                            "- get_butterfly_associations: {entity_id: str}\n"
                            "- create_skill: {name: str, node_ids: list, domain: str, description?: str}\n"
                            "- list_skills: {domain?: str}\n"
                            "- summarize_experience: {node_ids: list, format?: 'text'|'json'}"
                        ),
                    },
                },
                "required": ["operation", "params"],
            },
        }
