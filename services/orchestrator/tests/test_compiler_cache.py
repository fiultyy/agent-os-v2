"""Tests for P2 compiler three-layer compile + static prefix stability."""

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Patch corrupted miniconda sqlite3 with pysqlite3 before any import that
# transitively touches sqlite3 (src.context → pitfail → sqlite3). Mirrors
# start.py. No-op when pysqlite3 is unavailable.
try:
    import pysqlite3

    sys.modules["sqlite3"] = pysqlite3
    sys.modules["sqlite3.dbapi2"] = pysqlite3.dbapi2
except ImportError:
    pass

import pytest

from src.context.compiler import CompiledContext, ContextCompiler
from src.context.manager import ContextManager
from src.memory.service import MemoryService
from src.memory.store import InMemoryStore


def _compiler() -> ContextCompiler:
    svc = MemoryService(store=InMemoryStore())
    return ContextCompiler(ContextManager(svc))


class TestThreeLayerStructure:
    @pytest.mark.asyncio
    async def test_static_count_base_only(self) -> None:
        ctx = await _compiler().compile(system_prompt="SP", conversation=[])
        assert isinstance(ctx, CompiledContext)
        assert ctx.static_count == 1
        assert ctx.messages[0] == {"role": "system", "content": "SP"}

    @pytest.mark.asyncio
    async def test_static_count_with_tools(self) -> None:
        ctx = await _compiler().compile(
            system_prompt="SP",
            conversation=[],
            tool_defs=[{"name": "t1", "description": "d1"}],
        )
        assert ctx.static_count == 2  # base + tools
        assert ctx.messages[0]["content"] == "SP"
        assert "[Available tools]" in ctx.messages[1]["content"]

    @pytest.mark.asyncio
    async def test_memory_injected_into_user_tail(self) -> None:
        """R2: recalled memory is injected into the current user message
        tail (fenced), NOT as an independent role=system message."""
        cc = _compiler()
        await cc._manager._memory.store(
            content="SP memo about blue", agent_id="a", session_id="s"
        )
        ctx = await cc.compile(
            system_prompt="SP",
            conversation=[{"role": "user", "content": "what do you know?"}],
            agent_id="a",
            session_id="s",
            tool_defs=[{"name": "t", "description": "d"}],
        )
        assert ctx.static_count == 2  # base + tools only — memory is NOT static
        # memory rides inside the user message, not as an independent message
        user_msgs = [m for m in ctx.messages if m.get("role") == "user"]
        assert len(user_msgs) == 1
        assert "<memory-context>" in user_msgs[-1]["content"]
        assert "SP memo about blue" in user_msgs[-1]["content"]
        # original question preserved at the start of the user content
        assert user_msgs[-1]["content"].startswith("what do you know?")
        # no independent system memory message anywhere
        assert not any(
            "[Relevant memories]" in m.get("content", "") for m in ctx.messages
        )

    @pytest.mark.asyncio
    async def test_cache_breakpoint_param_accepted(self) -> None:
        ctx = await _compiler().compile(
            system_prompt="SP", conversation=[], cache_breakpoint=True
        )
        assert ctx.static_count == 1


class TestStaticStability:
    @pytest.mark.asyncio
    async def test_static_prefix_identical_across_turns(self) -> None:
        """Same base+tools → byte-identical static prefix, regardless of
        differing conversation/recall. This is the cache-friendly guarantee."""
        cc = _compiler()
        ctx1 = await cc.compile(
            system_prompt="SP",
            conversation=[{"role": "user", "content": "question one"}],
            tool_defs=[{"name": "t", "description": "d"}],
            session_id="",  # no recall → deterministic
        )
        ctx2 = await cc.compile(
            system_prompt="SP",
            conversation=[{"role": "user", "content": "totally different question two"}],
            tool_defs=[{"name": "t", "description": "d"}],
            session_id="",
        )
        assert ctx1.messages[: ctx1.static_count] == ctx2.messages[: ctx2.static_count]

    def test_get_static_context_stable(self) -> None:
        cc = _compiler()
        s1 = cc.get_static_context("SP", [{"name": "t", "description": "d"}])
        s2 = cc.get_static_context("SP", [{"name": "t", "description": "d"}])
        assert s1 == s2
        assert len(s1) == 2

    @pytest.mark.asyncio
    async def test_static_prefix_identical_with_memory_in_user(self) -> None:
        """R2 core: same base+tools → byte-identical static prefix even when
        recall + conversation differ (memory lives in the user tail, after
        the cache breakpoint)."""
        cc = _compiler()
        await cc._manager._memory.store(
            content="blue fact", agent_id="a", session_id="s1"
        )
        ctx1 = await cc.compile(
            system_prompt="SP",
            conversation=[{"role": "user", "content": "q1"}],
            agent_id="a",
            session_id="s1",
            tool_defs=[{"name": "t", "description": "d"}],
        )
        ctx2 = await cc.compile(
            system_prompt="SP",
            conversation=[{"role": "user", "content": "completely different q2"}],
            agent_id="a",
            session_id="",  # no recall → no memory block
            tool_defs=[{"name": "t", "description": "d"}],
        )
        assert ctx1.messages[: ctx1.static_count] == ctx2.messages[: ctx2.static_count]
        assert ctx1.static_count == ctx2.static_count == 2


class TestConcurrentCompile:
    """spec §4.1: multi-agent concurrent compile must not race —
    ContextManager.select is read-only (recall only)."""

    @pytest.mark.asyncio
    async def test_multi_agent_concurrent_compile_no_race(self) -> None:
        """spec §4.1: multi-agent concurrent compile must not race —
        ContextManager.select is read-only (recall only), so N agents
        compiling concurrently produce well-formed contexts with a stable,
        byte-identical static prefix (the cache-friendly guarantee holds
        under concurrency)."""
        cc = _compiler()
        for a in range(5):
            await cc._manager._memory.store(
                content=f"note for agent{a}", agent_id=f"a{a}", session_id=f"s{a}"
            )

        async def _one(a: int):
            return await cc.compile(
                system_prompt="SP",
                conversation=[{"role": "user", "content": f"q{a}"}],
                agent_id=f"a{a}",
                session_id=f"s{a}",
                tool_defs=[{"name": "t", "description": "d"}],
            )

        results = await asyncio.gather(*[_one(a) for a in range(5)])
        # every context well-formed; static prefix byte-identical across agents
        ref_prefix = results[0].messages[: results[0].static_count]
        for a, ctx in enumerate(results):
            assert ctx.static_count == 2
            assert ctx.messages[: ctx.static_count] == ref_prefix
            user_msgs = [m for m in ctx.messages if m.get("role") == "user"]
            assert len(user_msgs) == 1
            assert user_msgs[-1]["content"].startswith(f"q{a}")


class TestBudgetTruncation:
    @pytest.mark.asyncio
    async def test_conversation_truncated_to_budget(self) -> None:
        # tiny budget forces truncation of long conversation
        big = [{"role": "user", "content": "x" * 1000} for _ in range(50)]
        ctx = await _compiler().compile(
            system_prompt="SP", conversation=big, max_tokens=200
        )
        # not all 50 messages fit
        assert len(ctx.messages) < 50 + 1
