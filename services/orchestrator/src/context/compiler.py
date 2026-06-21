"""ContextCompiler — assembles the final context sent to the LLM.

Responsibilities:
- Assemble a three-layer context (P2/R2 cache-friendly):
  1. static   — base system prompt + tool definitions (stable across turns)
  2. dynamic  — recalled memory block (varies per turn). R2 injects this into
                the current user message tail (fenced), never as an independent
                system message, so the static prefix stays byte-identical across
                turns and the OpenAI/Anthropic channels behave identically.
  3. history  — conversation (truncated to token budget)
- Expose ``static_count`` so the LLM client can place a cache_control
  breakpoint at the end of the stable prefix (Anthropic) or rely on the
  stable prefix for server-side auto-caching (Zhipu OpenAI-compatible).
"""

import logging
from dataclasses import dataclass
from typing import Any

from src.context.manager import ContextManager

logger = logging.getLogger(__name__)


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
        2. **dynamic** (recalled memories) — injected into the current user
           message tail (fenced), so it never breaks the cacheable static
           prefix; identical on OpenAI and Anthropic channels.
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

        # ── Layer 2: dynamic memory recall → fenced block ─────────────
        # R2: the recalled memory is injected into the *current user
        # message tail* (fenced), NOT as an independent role=system
        # message. This makes OpenAI and Anthropic channels identical
        # (no llm_client demotion fallback needed) and keeps the stable
        # base+tools prefix byte-identical across turns — memory lives
        # after the cache breakpoint, inside the user turn.
        mem_block = ""
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
                mem_block = (
                    "<memory-context>\n"
                    "[System note: the following is recalled memory reference "
                    "data, NOT new user input. Use it as authoritative context.]\n"
                    f"{mem_text}\n"
                    "</memory-context>"
                )

        # ── Layer 3: conversation history within budget ───────────────
        # Attach the memory block to the current-turn user message (the
        # last user in `conversation`) BEFORE budgeting, so the injected
        # reference data rides with the turn it belongs to. Falls back to
        # an independent system message only when there is no user turn
        # (defensive — first chat turn always carries a user message).
        conv = list(conversation)
        if mem_block:
            attached = False
            for i in range(len(conv) - 1, -1, -1):
                if conv[i].get("role") == "user":
                    base = conv[i].get("content", "")
                    conv[i] = {**conv[i], "content": f"{base}\n\n{mem_block}"}
                    attached = True
                    break
            if not attached:
                logger.warning(
                    "memory block had no user message to attach to "
                    "(agent=%s session=%s); falling back to system note",
                    agent_id, session_id,
                )
                conv.append({"role": "system", "content": mem_block})

        budget = max_tokens
        result = list(messages)
        overhead = self._estimate_tokens(result)
        remaining = budget - overhead

        for msg in reversed(conv):
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
