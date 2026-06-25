# tests/unit/test_p0_fixes.py
"""P0 修复回归测试(对应记忆系统缺陷审计 P0 三项):

- P0-1 神经场接线:``NeuralHook._extract_concepts`` 从本轮 memory item 抽 concept
  喂 ``drift_turn``;此前硬编码空 concepts → 图召回节点 act 恒 0。
- P0-2 ``EntityExtractor`` CJK 中英文实体/关系抽取 —— 原版 7+7 正则全 ASCII,
  对纯中文内容零产出(致 KG=0 的结构性根因)。
- P0-3 ``KeywordRecall`` 近因兜底 —— query 非空但无字面命中时返回最近 top_k,
  消灭「问"之前聊过X"措辞不命中关键词 → 失忆」(纯加法,不动 KEYWORD 红线)。
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

try:
    import pysqlite3  # type: ignore[import-untyped]
    sys.modules["sqlite3"] = pysqlite3
    sys.modules["sqlite3.dbapi2"] = pysqlite3.dbapi2
except ImportError:
    pass

from memory._recall.keyword_recall import KeywordRecall  # noqa: E402
from memory.hooks import TurnContext  # noqa: E402
from memory.knowledge_graph import EntityExtractor  # noqa: E402
from memory.neural_field import NeuralFieldEngine, NeuralFieldStore, NeuralHook  # noqa: E402
from memory.store import InMemoryStore  # noqa: E402
from memory.types import MemoryItem  # noqa: E402


# ── helpers ────────────────────────────────────────────────────────────

def _fresh_store() -> NeuralFieldStore:
    path = os.path.join(tempfile.mkdtemp(), "nf-test.db")
    return NeuralFieldStore(db_path=path)


def _item(content: str, *, importance: float = 0.5, agent_id: str = "a") -> MemoryItem:
    return MemoryItem(
        id=content[:8],
        content=content,
        importance=importance,
        agent_id=agent_id,
    )


# ── P0-2: EntityExtractor CJK 实体/关系抽取 ────────────────────────────

class TestP02CJKExtraction:
    def test_cjk_quoted_entity_extracted(self):
        ex = EntityExtractor()
        names = [e.name for e in ex.extract_entities("《记忆系统》是核心模块,「蝴蝶翼」扩散。")]
        assert "记忆系统" in names
        assert "蝴蝶翼" in names

    def test_cjk_latin_mix_extracted(self):
        ex = EntityExtractor()
        names = [e.name for e in ex.extract_entities("Logseq 的 CLI 命令体系,glm-4.7 模型。")]
        # 中英混排技术词被抽到(原版对这种纯中文夹英文的术语也漏)
        assert len(names) > 0
        assert any("CLI" in n or "Logseq" in n for n in names)

    def test_cjk_relation_predicates(self):
        ex = EntityExtractor()
        rels = ex.extract_relations("Logseq 使用 CLI 命令,记忆系统依赖 向量数据库,CLI 属于 独立工具。")
        by_pred = {(r.source_entity_id, r.relation_type): r.target_entity_id for r in rels}
        assert ("Logseq", "uses") in by_pred
        assert any(r.relation_type == "depends_on" for r in rels)
        assert any(r.relation_type == "is_a" for r in rels)

    def test_cjk_pure_chinese_not_zero_output(self):
        """核心回归:纯中文记忆不再零产出(原版 7+7 正则 ZERO HITS)。"""
        ex = EntityExtractor()
        text = "蝴蝶翼扩散使用谱半径约束,《记忆系统》包含向量召回模块。"
        ents = ex.extract_entities(text)
        rels = ex.extract_relations(text)
        # 实体(书名号)或关系(谓词)至少一边有产出
        assert len(ents) > 0 or len(rels) > 0

    def test_ascii_patterns_still_work(self):
        """P0-2 是纯加法,原 ASCII 模式回归不破坏。"""
        ex = EntityExtractor()
        names = [e.name for e in ex.extract_entities("FastAPI and SQLAlchemy use connection_pool.")]
        assert any("FastAPI" in n for n in names)
        assert any("SQLAlchemy" in n for n in names)

    def test_cjk_no_noise_overflow(self):
        """SF-1 负向断言:收紧后不得有含句末标点/助词 或 len>12 的越界短语。

        评审 MF-1/MF-2 回归保护 —— 防止正则放松后重新吞中文散文。
        这是评审给的验收用例。
        """
        ex = EntityExtractor()
        text = ("Logseq 的 memory 系统是一个知识图谱工具,和 CLI 命令配合使用。"
                "这个项目使用 FastAPI,依赖 PostgreSQL。glm-4.7 是模型")
        ents = [e.name for e in ex.extract_entities(text)]
        rels = list(ex.extract_relations(text))
        for n in ents:
            assert len(n) <= 12, f"实体越界:{n!r}"
            assert not any(c in n for c in "。！？是的和"), f"实体含杂质:{n!r}"
        for r in rels:
            for ep in (r.source_entity_id, r.target_entity_id):
                assert len(ep) <= 12, f"关系端点越界:{ep!r}"
                assert not any(c in ep for c in "。！？"), f"关系端点含标点:{ep!r}"
        # 核心英文术语被抽到(确认不是全空)
        assert any("Logseq" in n or "CLI" in n or "FastAPI" in n for n in ents)

    def test_cjk_persist_to_kg(self):
        """SF-10 集成:CJK 实体/关系经 extract_and_ingest 落 SQLite KG(IngestorAgent 降级路径)。"""
        from memory.knowledge_graph import KnowledgeGraph

        kg = KnowledgeGraph(db_path=os.path.join(tempfile.mkdtemp(), "kg-cjk.db"))
        res = kg.extract_and_ingest(
            "Logseq 使用 CLI 命令,《记忆系统》依赖 向量数据库。", "mem-1",
        )
        assert len(res["entity_ids"]) >= 2, "CJK 实体未落库"
        assert len(res["relation_ids"]) >= 1, "CJK 关系未落库"
        s = kg.stats()
        assert s.get("entity_count", 0) > 0, f"stats 未反映实体: {s}"


# ── P0-3: KeywordRecall 近因兜底 ───────────────────────────────────────

class TestP03RecallFallback:
    def test_fallback_when_no_literal_match(self):
        """query 非空但无字面命中 → 返回最近 top_k(原版返回 [] → 失忆)。"""
        store = InMemoryStore()
        asyncio.run(store.store(_item("Logseq CLI 完整命令体系")))
        asyncio.run(store.store(_item("记忆系统语义层设计")))
        kr = KeywordRecall(store)

        results = asyncio.run(kr.recall(
            query="之前聊过", agent_id="a", session_id="",
            memory_type=None, scope=None, top_k=5,
        ))
        assert len(results) > 0  # P0-3: 不再失忆

    def test_still_returns_matches_when_hit(self):
        """兜底不影响正常命中路径。"""
        store = InMemoryStore()
        asyncio.run(store.store(_item("Logseq CLI 完整命令体系")))
        asyncio.run(store.store(_item("今天天气不错")))
        kr = KeywordRecall(store)

        results = asyncio.run(kr.recall(
            query="Logseq", agent_id="a", session_id="",
            memory_type=None, scope=None, top_k=5,
        ))
        assert any("Logseq" in r.content for r in results)
        # 不命中项不被兜底污染(有命中时走正常匹配路径)
        assert all("天气" not in r.content for r in results)

    def test_empty_query_returns_recent(self):
        """query 空 → 原行为(返回最近 top_k),不受兜底影响。"""
        store = InMemoryStore()
        asyncio.run(store.store(_item("alpha")))
        asyncio.run(store.store(_item("beta")))
        kr = KeywordRecall(store)

        results = asyncio.run(kr.recall(
            query="   ", agent_id="a", session_id="",
            memory_type=None, scope=None, top_k=5,
        ))
        assert len(results) == 2


# ── P0-1: NeuralHook concept 接线 ──────────────────────────────────────

class TestP01NeuralWiring:
    def test_extract_concepts_from_working_item(self):
        hook = NeuralHook(NeuralFieldEngine(), _fresh_store())
        ctx = TurnContext(
            agent_id="a", session_id="s",
            working_item=_item("Logseq CLI uses glm-4.7 模型", importance=0.5),
        )
        concepts, importances = hook._extract_concepts(ctx)
        assert len(concepts) > 0
        # importance floor 0.3,保证 drift 激活 step 对 concept 加 Δpotential>0
        assert all(v >= 0.3 for v in importances.values())
        assert set(concepts) == set(importances.keys())

    def test_extract_concepts_empty_when_no_item(self):
        hook = NeuralHook(NeuralFieldEngine(), _fresh_store())
        ctx = TurnContext(agent_id="a", session_id="s")
        assert hook._extract_concepts(ctx) == ([], {})

    def test_extract_concepts_empty_when_blank_content(self):
        hook = NeuralHook(NeuralFieldEngine(), _fresh_store())
        ctx = TurnContext(agent_id="a", session_id="s", working_item=_item(""))
        assert hook._extract_concepts(ctx) == ([], {})

    def test_on_turn_end_populates_field(self):
        """集成:P0-1 接线前 field 恒空,接线后 concept 被激活进 field。"""
        store = _fresh_store()
        hook = NeuralHook(NeuralFieldEngine(), store)
        ctx = TurnContext(
            agent_id="a", session_id="s",
            working_item=_item("FastAPI and SQLAlchemy use connection_pool", importance=0.6),
        )
        asyncio.run(hook.on_turn_end(ctx))
        state = store.load_state("a")
        assert state is not None
        assert len(state.field) > 0  # P0-1:神经场终于被喂数据
