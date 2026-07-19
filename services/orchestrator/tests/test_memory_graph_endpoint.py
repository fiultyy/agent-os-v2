"""阶段1 图召回端点 GET /v1/memory/graph 单测。

锁定:
- 响应 schema(nodes/edges/activated_path/meta)符合 spec §2
- nodes 含 lif_activation/match_score/composite_score/origin/state/wing
- edges 三类 rel 正确标注(kg_relation/lif_spread/butterfly_assoc)
- 图组装只读:RetrieverAgent.retrieve(detail=True) 透出明细且默认 detail=False
  保留老契约(红线:不破坏 RECALL hook / GET /memories)
- top_k + 激活阈值过滤生效(防膨胀)
- scope 透传到 retriever.retrieve(scope=...)
- 各组件 feature-gated 缺席时返回合法空图骨架(不 500)
"""

import os
import sys
from dataclasses import dataclass
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from src.api.routes import memory as memory_route
from src.services import _state
from src.memory.types import MemoryItem, MemoryOrigin, MemoryScope, MemoryState


def _run_sync(coro):
    """Run an async coroutine synchronously — robust to a poisoned main thread.

    py3.12 + pytest-asyncio: after an async test, ``set_event_loop(None)`` leaves
    the main thread without a loop and the deprecated ``get_event_loop()`` raises.
    Recreate the loop when missing/closed. See feedback-pytest-asyncio-loop-pollution.
    """
    import asyncio

    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("closed")
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


# ── Fakes ──────────────────────────────────────────────────────────


@dataclass
class _NeuralState:
    field: dict[str, float]
    attention_group: list[str]


class _FakeRetriever:
    """记录调用参数 + 返回 canned ranked(含 detail 明细)。"""

    def __init__(self, ranked: list[dict[str, Any]]) -> None:
        self._ranked = ranked
        self.calls: list[dict[str, Any]] = []

    async def retrieve(self, *, query, agent_id, top_k, lif_state, scope, detail):
        self.calls.append({
            "query": query, "agent_id": agent_id, "top_k": top_k,
            "lif_state": lif_state, "scope": scope, "detail": detail,
        })
        return self._ranked


class _FakeNeuralStore:
    def __init__(self, state, snap_id="snap-1") -> None:
        self._state = state
        self._snap_id = snap_id

    def load_state(self, agent_id):
        return self._state

    def latest_stable_snapshot_id(self, agent_id):
        return self._snap_id


class _FakeKG:
    """满足 _resolve_entity_id / get_entity / get_entity_relations / find_entity_by_name。"""

    def __init__(self, entities: dict[str, dict], relations: list[dict]) -> None:
        # entities: name -> {id, source_memory_ids}
        self._by_name = entities
        self._by_id = {v["id"]: {"name": k, **v} for k, v in entities.items()}
        self._relations = relations

    def find_entity_by_name(self, name):
        return self._by_name.get(name)

    def _resolve_entity_id(self, name_or_id):
        if name_or_id in self._by_id:
            return name_or_id
        for name, ent in self._by_name.items():
            if name.lower() == name_or_id.lower():
                return ent["id"]
        return None

    def get_entity(self, entity_id):
        return self._by_id.get(entity_id)

    def get_entity_relations(self, entity_id):
        out = []
        for r in self._relations:
            if r["source_id"] == entity_id or r["target_id"] == entity_id:
                out.append(r)
        return out


class _FakeButterflyStore:
    def __init__(self, wings: dict[str, Any]) -> None:
        self._wings = wings

    def load_wing(self, memory_id):
        return self._wings.get(memory_id)


# ── fixtures ───────────────────────────────────────────────────────


def _mem(mid, content, origin="agent", state="active", scope="agent"):
    return MemoryItem(
        id=mid, content=content, agent_id="a",
        origin=MemoryOrigin(origin), state=MemoryState(state),
        scope=MemoryScope(scope),
    )


@pytest.fixture
def isolated_state(monkeypatch):
    """快照 + 还原 _state 上的图端点依赖,避免污染其它测试。"""
    saved = {
        "retriever": _state.retriever,
        "neural_store": _state.neural_store,
        "knowledge_graph": _state.knowledge_graph,
    }
    # butterfly_wing._default_store 也需隔离(图端点读它)。
    from src.memory import butterfly_wing
    saved_bw = getattr(butterfly_wing, "_default_store", None)
    yield monkeypatch
    _state.retriever = saved["retriever"]
    _state.neural_store = saved["neural_store"]
    _state.knowledge_graph = saved["knowledge_graph"]
    butterfly_wing._default_store = saved_bw


# ── endpoint schema ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_graph_schema_and_node_fields(isolated_state):
    mem = _mem("m1", "react performance tuning")
    retriever = _FakeRetriever(ranked=[{
        "item": mem, "score": 0.8,
        "match_score": 0.8, "lif_weight": 1.0,
        "activated_entities": ["react"],
    }])
    isolated_state.setattr(_state, "retriever", retriever)
    isolated_state.setattr(_state, "neural_store", _FakeNeuralStore(
        _NeuralState(field={"react": 0.7}, attention_group=[]),
    ))
    isolated_state.setattr(_state, "knowledge_graph", _FakeKG(
        entities={"react": {"id": "e_react", "source_memory_ids": ["m1"]}},
        relations=[],
    ))

    out = await memory_route.memory_graph(
        entities="react", agent_id="a", scope="agent", top_k=5,
    )

    # 顶层 schema
    assert set(out.keys()) == {"query_entities", "nodes", "edges", "activated_path", "meta"}
    assert out["query_entities"] == ["react"]

    # 至少有一个 memory 节点,字段齐全
    mem_nodes = [n for n in out["nodes"] if n["kind"] == "memory"]
    assert mem_nodes, "应至少一个 memory 节点"
    n = mem_nodes[0]
    for f in ("id", "kind", "content", "memory_id", "lif_activation",
              "match_score", "composite_score", "origin", "state", "scope", "wing"):
        assert f in n, f"memory 节点缺字段 {f}"
    assert n["match_score"] == pytest.approx(0.8)
    assert n["composite_score"] == pytest.approx(0.8)
    assert n["origin"] == "agent"
    assert n["state"] == "active"

    # entity 节点 + activated_path
    ent_nodes = [n for n in out["nodes"] if n["kind"] == "entity"]
    assert any(n["content"] == "react" for n in ent_nodes)
    assert "react" in out["activated_path"]

    # meta
    assert out["meta"]["agent_id"] == "a"
    assert out["meta"]["scope"] == "agent"
    assert out["meta"]["lif_snapshot_id"] == "snap-1"
    assert out["meta"]["total_nodes"] == len(out["nodes"])


@pytest.mark.asyncio
async def test_edges_three_relation_types(isolated_state):
    """kg_relation / lif_spread / butterfly_assoc 三类边都能产出。"""
    mem1 = _mem("m1", "react state")
    mem2 = _mem("m2", "redux state")
    retriever = _FakeRetriever(ranked=[
        {"item": mem1, "score": 0.9, "match_score": 0.9, "lif_weight": 1.0,
         "activated_entities": ["react"]},
        {"item": mem2, "score": 0.5, "match_score": 0.5, "lif_weight": 1.0,
         "activated_entities": ["react"]},
    ])
    isolated_state.setattr(_state, "retriever", retriever)
    isolated_state.setattr(_state, "neural_store", _FakeNeuralStore(
        _NeuralState(field={"react": 0.8, "redux": 0.6}, attention_group=[]),
    ))
    kg = _FakeKG(
        entities={
            "react": {"id": "e_react", "source_memory_ids": ["m1"]},
            "redux": {"id": "e_redux", "source_memory_ids": ["m2"]},
        },
        relations=[{  # react --uses--> redux
            "source_id": "e_react", "target_id": "e_redux",
            "relation_type": "uses", "confidence": 0.9,
        }],
    )
    isolated_state.setattr(_state, "knowledge_graph", kg)

    # 蝴蝶翼:m1 forward-associate 到 m2
    from src.memory.butterfly_wing import ButterflyWing, WingMetadata, WingType
    wing_m1 = ButterflyWing(
        forward_metadata=WingMetadata(wing=WingType.FORWARD),
        forward_score=0.8, forward_associations=["m2"],
        backward_score=0.0, backward_associations=[],
    )
    isolated_state.setattr(
        _state, "knowledge_graph", kg,
    )
    # 注入 _default_store(图端点只读消费)
    from src.memory import butterfly_wing
    isolated_state.setattr(butterfly_wing, "_default_store", _FakeButterflyStore({"m1": wing_m1}))

    out = await memory_route.memory_graph(entities="react", agent_id="a", top_k=5)

    rels = {e["rel"] for e in out["edges"]}
    # react-redux 至少有 kg_relation 与 lif_spread(同一条无向 KG 边)
    assert "kg_relation" in rels
    # m1→m2 蝴蝶翼联想
    butterfly_edges = [e for e in out["edges"] if e["rel"] == "butterfly_assoc"]
    assert any(
        e["src"].startswith("mem::m1") and e["dst"].startswith("mem::m2")
        for e in butterfly_edges
    ), f"应产出 m1→m2 butterfly_assoc 边, 实际: {butterfly_edges}"


@pytest.mark.asyncio
async def test_scope_passed_through_and_threshold_filter(isolated_state):
    """scope 透传到 retriever.retrieve;低电位游离概念不进图(阈值过滤)。"""
    retriever = _FakeRetriever(ranked=[])
    isolated_state.setattr(_state, "retriever", retriever)
    isolated_state.setattr(_state, "neural_store", _FakeNeuralStore(
        _NeuralState(field={"hot": 0.9, "cold": 0.001}, attention_group=[]),
    ))
    isolated_state.setattr(_state, "knowledge_graph", _FakeKG(
        entities={"hot": {"id": "e_hot", "source_memory_ids": []}}, relations=[],
    ))

    out = await memory_route.memory_graph(entities="hot", agent_id="a", scope="private", top_k=3)

    assert retriever.calls[0]["scope"] == "private"
    assert retriever.calls[0]["detail"] is True
    # hot(0.9 > 0.05)入图,cold(0.001 < 0.05)被阈值过滤掉
    ent_contents = [n["content"] for n in out["nodes"] if n["kind"] == "entity"]
    assert "hot" in ent_contents
    assert "cold" not in ent_contents


@pytest.mark.asyncio
async def test_empty_graph_when_components_unwired(isolated_state):
    """retriever/neural/kg 全 None → 返回合法空图骨架,不抛。"""
    isolated_state.setattr(_state, "retriever", None)
    isolated_state.setattr(_state, "neural_store", None)
    isolated_state.setattr(_state, "knowledge_graph", None)
    from src.memory import butterfly_wing
    isolated_state.setattr(butterfly_wing, "_default_store", None)

    out = await memory_route.memory_graph(entities="x", agent_id="a", top_k=5)

    assert out["nodes"] == []
    assert out["edges"] == []
    assert out["meta"]["total_nodes"] == 0
    assert out["query_entities"] == ["x"]


# ── detail mode contract (设计点 a) ────────────────────────────────


def test_retrieve_detail_default_false_preserves_legacy_contract():
    """默认 detail=False ⇒ 老契约 [{item, score}],不携带明细字段。"""
    from src.memory.sideline.retriever_agent import RetrieverAgent

    class _Svc:
        async def recall(self, **kw):
            return [MemoryItem(id="m1", content="alpha beta", agent_id="a")]

    agent = RetrieverAgent(_Svc())
    ranked = _run_sync(
        agent.retrieve(query="alpha", agent_id="a", top_k=3)
    )
    assert len(ranked) == 1
    # 老契约只有 item + score
    assert set(ranked[0].keys()) == {"item", "score"}


def test_retrieve_detail_true_emits_extra_fields():
    """detail=True ⇒ 透出 match_score/lif_weight/activated_entities(图端点消费)。"""
    from src.memory.sideline.retriever_agent import RetrieverAgent

    class _Svc:
        async def recall(self, **kw):
            return [MemoryItem(id="m1", content="alpha beta", agent_id="a")]

    agent = RetrieverAgent(_Svc())
    ranked = _run_sync(
        agent.retrieve(query="alpha", agent_id="a", top_k=3, detail=True)
    )
    assert len(ranked) == 1
    assert {"item", "score", "match_score", "lif_weight", "activated_entities"} <= set(ranked[0].keys())
    # composite_score = match × lif = score(红线:不改排序权重)
    assert ranked[0]["score"] == pytest.approx(
        ranked[0]["match_score"] * ranked[0]["lif_weight"]
    )
