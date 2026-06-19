"""ContextCompiler — assembles the final context sent to the LLM.

Responsibilities:
- Assemble a three-layer context (P2 cache-friendly):
  1. static   — base system prompt + tool definitions (stable across turns)
  2. dynamic  — recalled memory block (varies per turn)
  3. history  — conversation (truncated to token budget)
- Expose ``static_count`` so the LLM client can place a cache_control
  breakpoint at the end of the stable prefix (Anthropic) or rely on the
  stable prefix for server-side auto-caching (Zhipu OpenAI-compatible).
"""

from dataclasses import dataclass
from typing import Any

from src.context.manager import ContextManager


@dataclass
class CompiledContext:
    """Result of :meth:`ContextCompiler.compile`.

    Attributes:
        messages: Final message list for the LLM call.
        static_count: Number of leading *static* messages (base prompt +
            tool defs). ``messages[:static_count]`` is the stable prefix
            that should be cached; ``messages[static_count:]`` is dynamic
            (memory recall + growing conversation) and must not be cached.
    """

    messages: list[dict[str, Any]]
    static_count: int


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
        cache_breakpoint: bool = False,
    ) -> CompiledContext:
        """Compile and return a :class:`CompiledContext`.

        Three-layer structure:
        1. **static** (base prompt + tool defs) — first, stable across
           turns → cache prefix.
        2. **dynamic** (recalled memories) — after static, varies per turn,
           never breaks the cacheable prefix.
        3. **history** (conversation) — appended within ``max_tokens``.

        Args:
            system_prompt: System-level instructions.
            conversation: Message history (role/content dicts).
            agent_id: Agent ID for memory recall.
            session_id: Session ID for memory recall.
            memory_refs: Optional specific memory refs to include.
            tool_defs: Tool definitions for the LLM.
            max_tokens: Token budget upper bound.
            cache_breakpoint: Hint that the caller wants the static prefix
                cached. ``static_count`` is always populated regardless, so
                the LLM client can decide based on provider/format.

        Returns:
            CompiledContext with the message list and static_count.
        """
        # ── Layer 1: static prefix (stable across turns) ──────────────
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
        ]
        static_count = 1

        # Tool definitions go into the static layer (before memory) so the
        # whole static prefix — base prompt + tools — is cacheable.
        if tool_defs:
            tool_text = "\n".join(
                f"- {t.get('name', 'unknown')}: {t.get('description', '')}"
                for t in tool_defs
            )
            messages.append({
                "role": "system",
                "content": f"[Available tools]\n{tool_text}",
            })
            static_count += 1

        # ── Layer 2: dynamic memory recall (after static) ─────────────
        # Placed AFTER the static prefix so a varying recall result never
        # invalidates the cacheable base+tools prefix.
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

        # ── Layer 3: conversation history within budget ───────────────
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

        return CompiledContext(messages=result, static_count=static_count)

    def get_static_context(
        self,
        system_prompt: str,
        tool_defs: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """Return the stable static prefix (base prompt + tool defs) only.

        No memory recall, no conversation — just the cacheable layer. Useful
        for cache-key computation and unit-testing prefix stability without
        triggering recall. Mirrors the static layer assembled by compile().
        """
        msgs: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
        ]
        if tool_defs:
            tool_text = "\n".join(
                f"- {t.get('name', 'unknown')}: {t.get('description', '')}"
                for t in tool_defs
            )
            msgs.append({"role": "system", "content": f"[Available tools]\n{tool_text}"})
        return msgs

    @staticmethod
    def _estimate_tokens(messages: list[dict[str, Any]]) -> int:
        """Rough token estimate: ~4 chars per token."""
        total = 0
        for msg in messages:
            content = msg.get("content", "")
            total += max(1, len(content) // 4)
        return total
