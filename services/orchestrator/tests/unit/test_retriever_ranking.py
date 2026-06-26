"""RetrieverAgent tie-breaker tests — score-equal low_info / importance 破结。

背景:b386243 的 KeywordRecall importance 排序在候选层执行,但被
UnifiedRecall._merge + RetrieverAgent.score 双层覆盖。RetrieverAgent.retrieve
原 sort 只按 score 降序——score 全等时 Python 稳定排序退回候选序,导致
失智回复 a405c48f(imp0.645 / low_info_reply=True)混入 #2,真维护
cc63ecb1(imp0.6718)压 #3。

修复(line 139 sort):score 仍降序(红线:line 131 entry["score"]=float(match*w)
不改),score 全等时按 low_info_reply 垫底 + importance 降序破结。

只验排序 tie-break 行为——不改 match_score / lif_weight / 五维评分红线。
"""

import os
import sys
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

try:
    import pysqlite3

    sys.modules["sqlite3"] = pysqlite3
    sys.modules["sqlite3.dbapi2"] = pysqlite3.dbapi2
except ImportError:
    pass

import pytest

from src.memory.sideline.retriever_agent import RetrieverAgent
from src.memory.types import MemoryItem


# ── Fixtures ────────────────────────────────────────────────────────


def _item(
    content: str,
    *,
    mid: str = "",
    importance: float = 0.5,
    low_info_reply: bool = False,
) -> MemoryItem:
    metadata: dict[str, Any] = {}
    if low_info_reply:
        metadata["low_info_reply"] = True
    return MemoryItem(
        id=mid or content[:8],
        content=content,
        agent_id="a",
        importance=importance,
        metadata=metadata,
    )


class _FakeRecallService:
    """Stand-in for MemoryService — returns a canned candidate list.

    Candidate *order* matters here: RetrieverAgent preserves candidate
    order on score ties unless the tie-breaker reorders, so feeding
    candidates in a deliberately bad order lets us assert the tie-break
    fired (rather than Python's stable sort leaving them as-is).

    NOTE: returns the *full* candidate list regardless of ``top_k``. The
    real MemoryService.recall may truncate, but the truncation that
    matters for tie-break behaviour is ``retrieve``'s own
    ``results[:top_k]`` (line 140) — which runs *after* the sort. Returning
    the full pool here lets us assert that tie-break reordering happens
    before that truncation (see test_top_k_truncation_after_tie_break).
    """

    def __init__(self, candidates: list[MemoryItem]) -> None:
        self._candidates = candidates
        self.kg = None  # pure keyword path

    async def recall(
        self, query: str = "", agent_id: str = "", top_k: int = 10, **kw: Any
    ) -> list[MemoryItem]:
        return list(self._candidates)


# ── Tie-break tests ────────────────────────────────────────────────


class TestScoreTieBreaker:
    @pytest.mark.asyncio
    async def test_score_diff_dominates(self) -> None:
        """score 不同的项仍按 score 降序——tie-breaker 不影响不同分项。

        构造:高分项(全 token 命中)恰好 low_info_reply=True + 低 importance,
        低分项(部分 token 命中)low_info_reply=False + 高 importance。
        tie-breaker 不得让低分项反超——score 是首要 sort key。
        """
        high_score_low_imp = _item(
            "deploy rust service notes",  # query 全 token 命中 → score 高
            mid="high",
            importance=0.1,
            low_info_reply=True,
        )
        low_score_high_imp = _item(
            "deploy notes only",  # 2/3 token 命中 → score 较低
            mid="low",
            importance=0.99,
            low_info_reply=False,
        )
        agent = RetrieverAgent(_FakeRecallService([high_score_low_imp, low_score_high_imp]))
        ranked = await agent.retrieve(query="deploy rust service", agent_id="a", top_k=2)

        # score 主导:高分项(即便 low_info + 低 importance)仍排第一。
        assert ranked[0]["item"].id == "high"
        assert ranked[0]["score"] > ranked[1]["score"]

    @pytest.mark.asyncio
    async def test_score_tie_low_info_sinks(self) -> None:
        """两项 score 相同,low_info_reply=True 的垫底。

        构造:两项对 query 命中完全一致(同 score),一项标 low_info_reply。
        候选序把 low_info 项放前面——tie-breaker 必须把它翻到后面。
        """
        good = _item("deploy rust service", mid="good", importance=0.5, low_info_reply=False)
        bad = _item("deploy rust service", mid="bad", importance=0.5, low_info_reply=True)
        # 候选序:bad 在前(模拟失智回复靠 UnifiedRecall 混入靠前位置)。
        agent = RetrieverAgent(_FakeRecallService([bad, good]))
        ranked = await agent.retrieve(query="deploy rust", agent_id="a", top_k=2)

        # score 全等,low_info 项垫底。
        assert ranked[0]["score"] == ranked[1]["score"]
        assert ranked[0]["item"].id == "good"
        assert ranked[1]["item"].id == "bad"
        assert (ranked[0]["item"].metadata or {}).get("low_info_reply") is not True

    @pytest.mark.asyncio
    async def test_score_tie_importance_desc(self) -> None:
        """两项 score 相同、都非 low_info,importance 高的排前。"""
        high_imp = _item("deploy rust service", mid="hi", importance=0.9, low_info_reply=False)
        low_imp = _item("deploy rust service", mid="lo", importance=0.2, low_info_reply=False)
        # 候选序:低 importance 在前——tie-breaker 翻转。
        agent = RetrieverAgent(_FakeRecallService([low_imp, high_imp]))
        ranked = await agent.retrieve(query="deploy rust", agent_id="a", top_k=2)

        assert ranked[0]["score"] == ranked[1]["score"]
        assert ranked[0]["item"].id == "hi"
        assert ranked[1]["item"].id == "lo"
        assert ranked[0]["item"].importance > ranked[1]["item"].importance

    @pytest.mark.asyncio
    async def test_score_tie_low_info_beats_importance(self) -> None:
        """tie-break 优先级:low_info 垫底先于 importance。

        low_info_reply=True 且高 importance 的项,仍排在 low_info_reply=False
        且低 importance 的项之后——失智标记是更强的下沉信号。
        """
        flagged_high_imp = _item(
            "deploy rust service", mid="flag", importance=0.99, low_info_reply=True
        )
        clean_low_imp = _item(
            "deploy rust service", mid="clean", importance=0.1, low_info_reply=False
        )
        agent = RetrieverAgent(_FakeRecallService([flagged_high_imp, clean_low_imp]))
        ranked = await agent.retrieve(query="deploy rust", agent_id="a", top_k=2)

        assert ranked[0]["score"] == ranked[1]["score"]
        # low_info 垫底优先于 importance:即便 0.99 vs 0.1,失智项仍排后。
        assert ranked[0]["item"].id == "clean"
        assert ranked[1]["item"].id == "flag"

    @pytest.mark.asyncio
    async def test_real_case_a405_vs_cc63(self) -> None:
        """复现 a405c48f(失智回复)vs cc63ecb1(真维护)score 全等场景。

        a405:  imp0.645,low_info_reply=True(失智回复"还是没想起来😅")
        cc63:  imp0.6718,low_info_reply=False(真维护)
        两者对 query 的字面命中相同 → match_score 全等 → 旧 sort 退候选序
        让 a405 靠 UnifiedRecall 混入靠前。tie-break 后 cc63 必须排前。
        """
        a405 = _item(
            "还是没想起来😅 我这边记忆库里确实没有任何关于 Logseq 的记录",
            mid="a405",
            importance=0.645,
            low_info_reply=True,
        )
        cc63 = _item(
            "Logseq 维护记录:每日 journal 用 outliner 结构沉淀",
            mid="cc63",
            importance=0.6718,
            low_info_reply=False,
        )
        # 候选序:a405 在前(模拟失智项靠 UnifiedRecall combined_score 混入靠前)。
        agent = RetrieverAgent(_FakeRecallService([a405, cc63]))

        # query 选一个让两项 match_score 全等的词——两项都含 "logseq"。
        # 单 token 命中 ⇒ 两项 match_score 相同(均 1/1,lif_weight=1.0)。
        ranked = await agent.retrieve(query="logseq", agent_id="a", top_k=2)

        # 前置断言:score 确实全等(否则不是 tie-break 场景)。
        assert ranked[0]["score"] == ranked[1]["score"], (
            "两项 match_score 应全等(同 token 命中),否则测试前提不成立"
        )
        # 真维护 cc63 必须排在失智 a405 之前。
        assert ranked[0]["item"].id == "cc63"
        assert ranked[1]["item"].id == "a405"

    @pytest.mark.asyncio
    async def test_top_k_truncation_after_tie_break(self) -> None:
        """tie-break 在 top_k 截断前生效——失智项被挤出 top_k 而非真维护。

        构造 3 项同 score,top_k=2:真维护 + 普通项 + 失智项。失智项应被截断。
        """
        real = _item("logseq note A", mid="real", importance=0.8, low_info_reply=False)
        plain = _item("logseq note B", mid="plain", importance=0.5, low_info_reply=False)
        dement = _item("logseq note C", mid="dement", importance=0.95, low_info_reply=True)
        agent = RetrieverAgent(_FakeRecallService([dement, plain, real]))
        ranked = await agent.retrieve(query="logseq", agent_id="a", top_k=2)

        assert len(ranked) == 2
        ids = [r["item"].id for r in ranked]
        # 失智项(即便 importance 最高)被 tie-break 挤出 top_k。
        assert "dement" not in ids
        assert "real" in ids and "plain" in ids
