"""P2 cache boundary reverse-budget — remaining_breakpoint_budget + llm_client
breakpoint counting.

Anthropic allows max 4 cache_control breakpoints per request. Native
ModelSettings already consumes 2 (instructions + tool_definitions); the
reverse budget ``max(0, 4 - sys - tool)`` is what the messages-side
``static_count`` region may use.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

try:
    import pysqlite3

    sys.modules["sqlite3"] = pysqlite3
    sys.modules["sqlite3.dbapi2"] = pysqlite3.dbapi2
except ImportError:
    pass

from src.services.prompt_cache import remaining_breakpoint_budget
from src.services.llm_client import LLMClient


class TestRemainingBreakpointBudget:
    def test_zero_zero_leaves_full_four(self) -> None:
        assert remaining_breakpoint_budget(0, 0) == 4

    def test_one_zero(self) -> None:
        assert remaining_breakpoint_budget(1, 0) == 3

    def test_one_one(self) -> None:
        # native default: anthropic_cache_instructions + tool_definitions
        assert remaining_breakpoint_budget(1, 1) == 2

    def test_two_two(self) -> None:
        assert remaining_breakpoint_budget(2, 2) == 0

    def test_clamp_negative_to_zero(self) -> None:
        # over-budget → clamp, never negative
        assert remaining_breakpoint_budget(3, 3) == 0
        assert remaining_breakpoint_budget(5, 0) == 0


class TestLLMClientBreakpointCount:
    """llm_client._chat_anthropic counts sys=1 + tool=1 markers consumed by
    native ModelSettings before placing its messages-side breakpoint."""

    def test_budget_gates_messages_side_marker(self, monkeypatch) -> None:
        # Default route: native consumed (1,1) → budget 2 > 0 → marker placed.
        client = LLMClient(format="anthropic", anthropic_api_key="k")
        captured: dict = {}

        def spy(messages, sc, ttl):
            captured["called"] = True
            captured["sc"] = sc
            return messages  # short-circuit; we only assert it was called

        monkeypatch.setattr("src.services.llm_client.apply_cache_control", spy)

        # Stub the httpx POST so we never hit network.
        async def fake_post(self, url, headers=None, json=None):
            class R:
                status_code = 200

                def raise_for_status(self):
                    pass

                def json(self):
                    return {"content": [{"type": "text", "text": "ok"}], "usage": {}}

            return R()

        import asyncio
        import httpx

        monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

        msgs = [
            {"role": "system", "content": "base"},
            {"role": "system", "content": "tools"},
            {"role": "user", "content": "hi"},
        ]
        asyncio.run(client.chat(msgs, static_count=2))

        assert captured.get("called") is True
        assert captured.get("sc") == 2
