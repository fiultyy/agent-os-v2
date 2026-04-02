"""ContextCompiler — assembles the final context sent to the LLM.

Responsibilities:
- Select relevant memory slices
- Inject system prompt + tool definitions
- Apply token budget constraints
- Handle compaction when context exceeds limits
"""

from typing import Any


class ContextCompiler:
    """Compiles a complete context window from multiple sources."""

    async def compile(
        self,
        system_prompt: str,
        conversation: list[dict[str, Any]],
        memory_refs: list[str],
        tool_defs: list[dict[str, Any]],
        max_tokens: int = 128_000,
    ) -> list[dict[str, Any]]:
        """Compile and return the final message list for the LLM call."""
        # TODO: implement context compilation
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
        ]
        return messages
