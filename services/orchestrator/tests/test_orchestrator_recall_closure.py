"""ADR-3 召回侧闭环验证 — 写侧(orchestrator 综合)→ 召回往返实证。

背景
    召回侧闭环机制已闭合:召回链全程 ``agent_id`` 严格相等过滤
    (store.py:149 / keyword_recall.py:25 / kg_recall.py:51 /
    retriever_agent.py:102-109),在评分排序上游 → orchestrator_id 沉淀
    (ADR-3 b721532)天然可被召回。红线 none(match×lif/五维/蝴蝶翼/origin
    零触碰)。残余仅缺实证 e2e —— 现有 ``test_multi_agent_orchestrate.py``
    只测写侧 R1(分支零 memory emit),无召回断言。本文件补这一实证。

覆盖
    1. 写侧 — ``_emit_orchestrator_synthesis_memory`` 经真实 ``MemoryEventBus``
       + ``DefaultMemoryHook`` → ``migrator.migrate_working_to_session`` →
       ``service.store(origin=AGENT)``。
    2. 召回往返 — ``memory_service.recall(query, agent_id=orch-1)`` 返回该综合记忆。
    3. GET /v1/memory/graph 往返 — ``memory_graph`` 端点 nodes[memory] 含该节点 +
       ``composite_score = match_score × lif_weight`` 非 0。
    4. 隔离负向 — ``agent_id="other-agent"`` 召回不含 orch-1 记忆(agent_id 严格过滤)。

对抗断言(红线守卫)
    - ``RetrieverAgent.retrieve(detail=True)`` 透出 ``match_score`` / ``lif_weight``
      / ``composite_score`` 三字段,且 ``composite ≈ match × lif``
      (红线 retriever_agent.py:131 守住)。
    - 综合记忆经迁移后 ``origin == MemoryOrigin.AGENT``(provenance 卫生,与
      /chat 对齐;经 default_hook → migrator.migrate_working_to_session)。
    - 分支 subagent agent_id 记忆不混入(跨 agent 隔离)。

红线(只读,绝不碰)
    - retriever_agent.py:131/148-154(match×lif + tie-breaker)
    - migrator.py(origin=AGENT 迁移)
    - butterfly_wing.py / keyword_recall.py / kg_recall.py / store.py:149

pysqlite3 注入由 ``tests/conftest.py`` 统一处理,本文件不重复 patch。
零 src 改动。
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from src.api.routes import memory as memory_route
from src.memory.compressor import AsyncCompressor, ContextMonitor, SyncCompressor
from src.memory.default_hook import DefaultMemoryHook
from src.memory.event_bus import MemoryEventBus
from src.memory.migrator import MemoryMigrator
from src.memory.service import MemoryService
from src.memory.sideline.retriever_agent import RetrieverAgent
from src.memory.store import InMemoryStore
from src.memory.types import MemoryOrigin
from src.orchestration.multi_agent_graph import _emit_orchestrator_synthesis_memory
from src.services import _state


# ── fixture:装配真实 MemoryEventBus + MemoryService(写→召回往返链) ──────


@pytest.fixture
def recall_closure_state():
    """备份/还原 _state 关键字段,装配最小真实写→召回链。

    真实组件(非 fake):
      - ``MemoryService(InMemoryStore())`` —— 无 KG(KEYWORD 模式召回,
        与生产 /chat 默认 RecallMode.KEYWORD 同形态)。
      - ``MemoryMigrator`` —— working→session 迁移,产出 ``origin=AGENT``。
      - ``ContextMonitor`` / ``SyncCompressor`` / ``AsyncCompressor`` ——
        DefaultMemoryHook 构造依赖(本测试不触发压缩,但需注入避免 None 报错)。
      - ``MemoryEventBus`` 注册 ``DefaultMemoryHook`` —— TURN_END 真实落库。
      - ``RetrieverAgent`` —— 挂 ``_state.retriever`` 供 graph 端点消费。

    统一注入 ``_state``,使模块级 ``_emit_orchestrator_synthesis_memory``
    helper 取到真实 bus / knowledge_graph(本测试 KG 留 None,helper ③ 段
    KG extract 静默 no-op)。
    """
    saved = {
        "memory_event_bus": _state.memory_event_bus,
        "memory_service": _state.memory_service,
        "knowledge_graph": _state.knowledge_graph,
        "retriever": getattr(_state, "retriever", None),
        "neural_store": getattr(_state, "neural_store", None),
    }

    store = InMemoryStore()
    memory_service = MemoryService(store=store, knowledge_graph=None)
    migrator = MemoryMigrator(memory_service)
    monitor = ContextMonitor()
    sync_compressor = SyncCompressor(monitor=monitor)
    async_compressor = AsyncCompressor(monitor=monitor)

    bus = MemoryEventBus()
    bus.register(
        DefaultMemoryHook(
            memory_service=memory_service,
            memory_migrator=migrator,
            sync_compressor=sync_compressor,
            async_compressor=async_compressor,
            context_monitor=monitor,
            write_queue=None,  # inline 写入(测试同步可观测)
        )
    )

    _state.memory_event_bus = bus
    _state.memory_service = memory_service
    _state.knowledge_graph = None
    _state.retriever = RetrieverAgent(memory_service=memory_service, kg=None)
    _state.neural_store = None  # Part 1 纯 match 模式(lif_weight=1.0)

    yield {
        "bus": bus,
        "memory_service": memory_service,
        "store": store,
    }

    for k, v in saved.items():
        setattr(_state, k, v)


async def _drain(seconds: float = 0.15) -> None:
    """排空 fire-and-forget emit(``asyncio.create_task``)产生的待办 task。

    ``_emit_orchestrator_synthesis_memory`` 用 ``create_task`` 非阻塞触发
    TURN_END / SESSION_END;helper 返回后事件尚未跑完,需 ``await sleep`` 让
    事件循环把它们调度完(DefaultMemoryHook 内 inline 写入 → store 落库)。
    """
    await asyncio.sleep(seconds)


# ── 1. 写侧:emit → store 落库 + origin=AGENT(provenance 卫生)─────────


@pytest.mark.asyncio
async def test_emit_sediments_orchestrator_memory_origin_agent(recall_closure_state):
    """写侧:helper emit 后 orchestrator 综合记忆落库,``origin=AGENT``。

    证据:
    - store 里出现 content 含 "先 build 再 deploy" 的 SESSION 记忆。
    - agent_id == orchestrator_id(orch-1)。
    - origin == MemoryOrigin.AGENT(经 default_hook → migrator.migrate_working_to_session,
      migrator 强制 origin=AGENT,与 /chat 对齐;provenance 卫生红线守住)。
    """
    svc: MemoryService = recall_closure_state["memory_service"]
    _emit_orchestrator_synthesis_memory(
        orchestrator_id="orch-1",
        session_id="s1",
        user_input="部署步骤",
        assistant_response="先 build 再 deploy",
    )
    await _drain()

    # 取 orch-1 全量记忆(无 query,KEYWORD 模式近因全量)。
    items = await svc.recall(query="", agent_id="orch-1", session_id="", top_k=50)
    targets = [m for m in items if "先 build 再 deploy" in (m.content or "")]
    assert targets, (
        f"orchestrator 综合记忆未落库:got {[(m.content, m.agent_id) for m in items]}"
    )
    mem = targets[0]
    # agent_id 严格 == orchestrator(ADR-3 单点 emit)。
    assert mem.agent_id == "orch-1"
    # provenance 卫生:经 migrator 迁移,origin 必为 AGENT。
    assert mem.origin == MemoryOrigin.AGENT, (
        f"origin must be AGENT after migrator, got {mem.origin!r}"
    )


# ── 2. 召回往返:memory_service.recall 命中综合记忆 ─────────────────────


@pytest.mark.asyncio
async def test_recall_roundtrip_finds_orchestrator_memory(recall_closure_state):
    """召回往返:``recall(query="部署", agent_id="orch-1")`` 返回该综合记忆。

    闭环证据(召回链全程 agent_id 严格过滤):
    - 写侧 emit 落库 → 召回 query 命中 → 返回列表含综合记忆。
    - content 含 "先 build 再 deploy"。
    """
    svc: MemoryService = recall_closure_state["memory_service"]
    _emit_orchestrator_synthesis_memory(
        orchestrator_id="orch-1",
        session_id="s1",
        user_input="部署步骤",
        assistant_response="先 build 再 deploy",
    )
    await _drain()

    results = await svc.recall(query="部署", agent_id="orch-1", top_k=10)
    contents = [m.content for m in results]
    assert any("先 build 再 deploy" in c for c in contents), (
        f"recall 未命中综合记忆:got {contents}"
    )


# ── 3. 隔离负向:agent_id 严格过滤,跨 agent 不泄漏 ────────────────────


@pytest.mark.asyncio
async def test_recall_isolation_other_agent_excludes_orch_memory(
    recall_closure_state,
):
    """隔离负向:``agent_id="other-agent"`` 召回**不含** orch-1 综合记忆。

    证召回链 ``agent_id`` 严格相等过滤(keyword_recall.py:25 MemoryFilter
    agent_id 等值),非泄漏。同时造一条 other-agent 自有记忆,证 other-agent
    只召回自己的记忆(跨 agent 隔离双向成立)。
    """
    svc: MemoryService = recall_closure_state["memory_service"]
    _emit_orchestrator_synthesis_memory(
        orchestrator_id="orch-1",
        session_id="s1",
        user_input="部署步骤",
        assistant_response="先 build 再 deploy",
    )
    # other-agent 自有记忆(直接 store,不假手 helper)。
    await svc.store(
        content="other agent 私有部署记录 部署",
        agent_id="other-agent",
        session_id="s-other",
    )
    await _drain()

    other_results = await svc.recall(query="部署", agent_id="other-agent", top_k=10)
    other_contents = [m.content for m in other_results]

    # orch-1 综合记忆绝不混入 other-agent 召回(严格过滤)。
    assert not any("先 build 再 deploy" in c for c in other_contents), (
        f"agent_id 隔离失败:orch-1 记忆泄漏进 other-agent 召回: {other_contents}"
    )
    # 所有返回项 agent_id 都是 other-agent(无任何 orch-1)。
    assert all(m.agent_id == "other-agent" for m in other_results), (
        f"跨 agent 隔离破损:返回项 agent_id 非 other-agent: "
        f"{[m.agent_id for m in other_results]}"
    )
    # other-agent 自有记忆可被自己召回(双向成立,非全空)。
    assert any("other agent 私有部署记录" in c for c in other_contents), (
        f"other-agent 自有记忆未被召回(召回链不应全空): {other_contents}"
    )


# ── 4. GET /v1/memory/graph 往返 + composite_score 公式 ───────────────


@pytest.mark.asyncio
async def test_graph_endpoint_roundtrip_and_composite_score(recall_closure_state):
    """GET /v1/memory/graph 往返:nodes[memory] 含综合记忆 + composite 非 0。

    闭环证据(graph 端点组装路径):
    - 写侧 emit 落库后,``memory_graph(entities="部署", agent_id="orch-1")``
      返回 nodes 含该综合记忆节点。
    - 该节点 ``composite_score`` 非 0(match×lif 产物:Part 1 lif_weight=1.0,
      content 含 query "部署" → match_score > 0 → composite > 0)。
    - 节点 ``origin == "agent"``(provenance 卫生透出)。
    """
    _emit_orchestrator_synthesis_memory(
        orchestrator_id="orch-1",
        session_id="s1",
        user_input="部署步骤",
        assistant_response="先 build 再 deploy",
    )
    await _drain()

    out = await memory_route.memory_graph(
        entities="部署", agent_id="orch-1", scope="", top_k=10,
    )
    memory_nodes = [n for n in out["nodes"] if n.get("kind") == "memory"]
    targets = [
        n for n in memory_nodes if "先 build 再 deploy" in (n.get("content") or "")
    ]
    assert targets, (
        f"graph 端点 nodes[memory] 未含综合记忆:got memory_nodes="
        f"{[n.get('content') for n in memory_nodes]}"
    )
    node = targets[0]
    # composite_score = match × lif 非 0(KEYWORD 命中 + Part 1 lif=1.0)。
    assert node["composite_score"] > 0.0, (
        f"composite_score must be > 0 (match×lif 产物), got {node['composite_score']}"
    )
    # origin 透出为 agent(provenance 卫生,经 migrator)。
    assert node["origin"] == "agent", (
        f"origin must be 'agent' (migrator provenance), got {node['origin']!r}"
    )


@pytest.mark.asyncio
async def test_graph_endpoint_isolation_other_agent_empty(recall_closure_state):
    """graph 端点隔离:``agent_id="other-agent"`` 召回不含 orch-1 节点。

    ``memory_graph`` 端点经 RetrieverAgent → service.recall,候选层 agent_id
    严格过滤,故 other-agent 的图里绝无 orch-1 综合记忆节点。
    """
    _emit_orchestrator_synthesis_memory(
        orchestrator_id="orch-1",
        session_id="s1",
        user_input="部署步骤",
        assistant_response="先 build 再 deploy",
    )
    await _drain()

    out = await memory_route.memory_graph(
        entities="部署", agent_id="other-agent", scope="", top_k=10,
    )
    memory_nodes = [n for n in out["nodes"] if n.get("kind") == "memory"]
    leaked = [
        n for n in memory_nodes if "先 build 再 deploy" in (n.get("content") or "")
    ]
    assert leaked == [], (
        f"graph 端点跨 agent 泄漏:other-agent 图里出现 orch-1 记忆: "
        f"{[n.get('content') for n in leaked]}"
    )


# ── 5. 对抗:RetrieverAgent.retrieve(detail=True) 公式 + 字段透出 ──────


@pytest.mark.asyncio
async def test_retriever_detail_match_times_lif_formula(recall_closure_state):
    """红线守卫:``RetrieverAgent.retrieve(detail=True)`` 公式未改。

    证据(retriever_agent.py:131 ``entry["score"]=float(match*w)`` 红线):
    - detail=True 透出 ``match_score`` / ``lif_weight`` / ``score`` 三字段。
    - 每项 ``score ≈ match_score × lif_weight``(允许 1e-6 浮点误差)。
    - 综合记忆命中(query="部署"),其 match_score > 0。
    - 结果按 score 降序(红线排序 key=-r["score"])。
    """
    svc: MemoryService = recall_closure_state["memory_service"]
    retriever: RetrieverAgent = _state.retriever

    _emit_orchestrator_synthesis_memory(
        orchestrator_id="orch-1",
        session_id="s1",
        user_input="部署步骤",
        assistant_response="先 build 再 deploy",
    )
    await _drain()

    ranked = await retriever.retrieve(
        query="部署", agent_id="orch-1", top_k=10, detail=True,
    )
    assert ranked, "RetrieverAgent 返回空(query 应命中综合记忆)"

    for r in ranked:
        # 三字段透出(detail=True 契约)。
        assert "match_score" in r, f"detail 透出缺 match_score: {r.keys()}"
        assert "lif_weight" in r, f"detail 透出缺 lif_weight: {r.keys()}"
        assert "score" in r, f"detail 透出缺 score(composite): {r.keys()}"
        match = float(r["match_score"])
        lif = float(r["lif_weight"])
        score = float(r["score"])
        # 红线:score = match × lif_weight(retriever_agent.py:131)。
        assert abs(score - match * lif) < 1e-6, (
            f"composite ≈ match × lif 红线破损: score={score} "
            f"match={match} lif={lif} (match×lif={match*lif})"
        )

    # 降序断言(红线 key=-r["score"])。
    scores = [float(r["score"]) for r in ranked]
    assert scores == sorted(scores, reverse=True), (
        f"ranked 未按 score 降序: {scores}"
    )

    # 综合记忆命中且 match_score > 0。
    synth = [
        r for r in ranked if "先 build 再 deploy" in (r["item"].content or "")
    ]
    assert synth, (
        f"RetrieverAgent 未召回综合记忆(query=部署): "
        f"{[r['item'].content for r in ranked]}"
    )
    assert synth[0]["match_score"] > 0.0


# ── 6. 对抗:subagent agent_id 记忆不混入 orchestrator 召回 ───────────


@pytest.mark.asyncio
async def test_subagent_memory_does_not_leak_into_orchestrator_recall(
    recall_closure_state,
):
    """跨 agent 隔离:分支 subagent agent_id 的记忆不混入 orchestrator 召回。

    模拟 ADR-3 场景:若(违反地)有 subagent agent_id 的记忆被写入,orchestrator
    召回 query=部署 **绝不应**返回 subagent 记忆 —— 召回链 agent_id 严格相等
    过滤是隔离的最后一道闸。
    """
    svc: MemoryService = recall_closure_state["memory_service"]
    _emit_orchestrator_synthesis_memory(
        orchestrator_id="orch-1",
        session_id="s1",
        user_input="部署步骤",
        assistant_response="先 build 再 deploy",
    )
    # 模拟一条 subagent agent_id 的部署记忆(假设泄漏写入)。放**不同 session**
    # —— 本测试断言召回链 agent_id 严格相等过滤,非 migrator session 级合并
    # (session 内合并按 session_id 聚合,与召回链 agent_id 过滤是两道独立闸)。
    await svc.store(
        content="subagent 私有部署笔记 部署",
        agent_id="subagent-role-a",
        session_id="s-subagent",
        origin=MemoryOrigin.AGENT,
    )
    await _drain()

    orch_results = await svc.recall(query="部署", agent_id="orch-1", top_k=20)
    orch_agent_ids = {m.agent_id for m in orch_results}
    # orchestrator 召回绝不混入 subagent 记忆。
    assert orch_agent_ids == {"orch-1"}, (
        f"subagent 记忆混入 orchestrator 召回(隔离破损): agent_ids={orch_agent_ids}"
    )
    assert not any(
        "subagent 私有部署笔记" in (m.content or "") for m in orch_results
    ), "subagent 记忆内容泄漏进 orchestrator 召回"
