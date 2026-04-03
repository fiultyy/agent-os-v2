"""ContextCompiler — assembles the final context sent to the LLM.

Responsibilities:
- Select relevant memory slices
- Inject system prompt + tool definitions
- Apply token budget constraints
- Handle compaction when context exceeds limits
"""

from typing import Any

from src.context.manager import ContextManager


class ContextCompiler:
    """Compiles a complete context window from multiple sources.

    Orchestrates memory recall, context selection, and compression
    to produce a minimal but sufficient context for the LLM.
    """

    def __init__(self, context_manager: ContextManager) -> None:
        self._manager = context_manager

    async def compile(
        self,
        system_prompt: str,
        conversation: list[dict[str, Any]],
        agent_id: str = "",
        session_id: str = "",
        memory_refs: list[str] | None = None,
        tool_defs: list[dict[str, Any]] | None = None,
        max_tokens: int = 128_000,
    ) -> list[dict[str, Any]]:
        """Compile and return the final message list for the LLM call.

        Strategy:
        1. Start with the system prompt.
        2. Recall relevant memories and inject them as a context block.
        3. Append tool definitions if present.
        4. Append conversation history.
        5. Truncate if total exceeds max_tokens.

        Args:
            system_prompt: System-level instructions.
            conversation: Message history (role/content dicts).
            agent_id: Agent ID for memory recall.
            session_id: Session ID for memory recall.
            memory_refs: Optional specific memory refs to include.
            tool_defs: Tool definitions for the LLM.
            max_tokens: Token budget upper bound.

        Returns:
            List of message dicts ready for the LLM.
        """
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
        ]

        # Recall relevant memories
        if session_id:
            recalled = await self._manager.select(
                session_id=session_id,
                query=system_prompt[:200],
                agent_id=agent_id,
                top_k=5,
            )
            if recalled:
                mem_text = "\n".join(
                    f"- [{r.get('created_at', '')[:10]}] {r['content'][:200]}"
                    for r in recalled
                )
                messages.append({
                    "role": "system",
                    "content": f"[Relevant memories]\n{mem_text}",
                })

        # Inject tool definitions
        if tool_defs:
            tool_text = "\n".join(
                f"- {t.get('name', 'unknown')}: {t.get('description', '')}"
                for t in tool_defs
            )
            messages.append({
                "role": "system",
                "content": f"[Available tools]\n{tool_text}",
            })

        # Add conversation history within budget
        budget = max_tokens
        result = list(messages)
        overhead = self._estimate_tokens(result)
        remaining = budget - overhead

        for msg in reversed(conversation):
            msg_tokens = self._estimate_tokens([msg])
            if remaining - msg_tokens < 0:
                break
            result.append(msg)
            remaining -= msg_tokens

        return result

    @staticmethod
    def _estimate_tokens(messages: list[dict[str, Any]]) -> int:
        """Rough token estimate: ~4 chars per token."""
        total = 0
        for msg in messages:
            content = msg.get("content", "")
            total += max(1, len(content) // 4)
        return total
