"""Memory, Permission, and Communication API routes."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Query

from src.api.models import (
    StoreMemoryRequest,
    GrantPermissionRequest,
    SendMessageRequest,
    MemoryNotifyRequest,
    MemoryConsolidateRequest,
)
from src.memory import MemoryType, MemoryScope
from src.memory.event_bus import EventType
from src.memory.hooks import (
    ConsolidateContext,
    CurateContext,
    IngestContext,
    RecallContext,
)
from src.memory.types import MemoryOrigin
from src.services import _state
from src.communication.message import AgentMessage, MessageType, MessagePriority

router = APIRouter()


# ── Memory API ─────────────────────────────────────────────────────


@router.post("/memories")
async def store_memory(req: StoreMemoryRequest, origin: str = "foreground") -> dict:
    """Store a new memory item.

    ``origin`` provenance (P0 red-line decisive fix):
    - ``foreground`` (default): user / external-app-authored content. P0-
      protected — NEVER auto-digested by the deterministic chain, the idle
      IngestorAgent, or the ConsolidatorAgent. Preserves legacy behaviour
      (old POSTs with no query param behave identically).
    - ``agent``: agent-self-sedimented content (e.g. an external harness like
      openclaw mirroring an agent's dialogue). Marks the row ``origin=AGENT``
      so the MemoryDBWatcher idle-trigger can extract / consolidate it.

    When ``sync_extract`` is True the route awaits the ① IngestorAgent LLM
    extraction (via ``EventType.INGEST``) and returns the
    entities/identity_category the agent produced. When False (default) the
    store returns immediately and ingestion is fire-and-forget.
    """
    mem_origin = MemoryOrigin(origin)
    ref = await _state.memory_service.store(
        content=req.content,
        agent_id=req.agent_id,
        session_id=req.session_id,
        memory_type=MemoryType(req.memory_type),
        scope=MemoryScope(req.scope),
        importance=req.importance,
        origin=mem_origin,
    )

    # The store endpoint defaults to FOREGROUND (user / external-app-authored
    # → P0-protected). The IngestorAgent P0 red-line returns early on
    # FOREGROUND, so emitting INGEST here is only meaningful when the caller
    # marks the content agent-self-sedimented (origin=agent) — sync_extract
    # then carries the SAME origin so an agent-marked row is digested inline.
    ingest_extras: dict[str, Any] = {}
    if req.sync_extract:
        ctx = IngestContext(
            memory_id=ref.id,
            content=req.content,
            agent_id=req.agent_id,
            session_id=req.session_id,
            origin=mem_origin.value,
        )
        result = await _state.memory_event_bus.emit(EventType.INGEST, ctx)
        if result is not None:
            # Mark the just-ingested memory ``metadata.extracted=True`` so the
            # idle-trigger IngestorAgent pass does not re-extract it as pending.
            # Re-get latest + merge (NOT whole-replace): the IngestorAgent just
            # wrote ``metadata.identity_category`` / ``metadata.degraded`` to the
            # same row, and on the SQLite backend search/get returns deserialized
            # copies while update whole-replaces the metadata column — a stale
            # snapshot or a bare {"extracted": True} would clobber identity_category.
            try:
                latest = await _state.memory_service.get(ref.id)
                if latest is not None:
                    base_meta = dict(getattr(latest, "metadata", None) or {})
                    base_meta["extracted"] = True
                    await _state.memory_service.update(ref.id, metadata=base_meta)
            except Exception:
                # Non-fatal: the cadence fix is the core; a missed extracted mark
                # only means a redundant re-extract on the next idle window.
                ingest_extras.setdefault("errors", []).append("sync_extract_mark_failed")
            ingest_extras = {
                "entities_added": getattr(result, "entities_added", 0),
                "relations_added": getattr(result, "relations_added", 0),
                "importance": getattr(result, "importance", None),
                "identity_category": getattr(result, "identity_category", "NONE"),
                "degraded": getattr(result, "degraded", False),
            }

    resp: dict[str, Any] = {
        "id": ref.id,
        "memory_type": ref.memory_type.value,
        "scope": ref.scope.value,
    }
    resp.update(ingest_extras)
    return resp


def _mem_to_dict(m: Any, score: float | None = None) -> dict:
    d = {
        "id": m.id,
        "agent_id": m.agent_id,
        "session_id": m.session_id,
        "memory_type": m.memory_type.value,
        "scope": m.scope.value,
        "content": m.content,
        "importance": m.importance,
        "created_at": m.created_at,
        "archived": m.archived,
        # origin: foreground(用户/外部 harness 保护写) vs agent(自主沉淀)。
        # P0 provenance 透出,让消费端区分"用户保护记忆 vs agent 沉淀"。
        "origin": m.origin.value,
        # state: P3 确定性生命周期(active/stale/archived)。getattr 防御未带 state 的旧对象,
        # 让消费端识别 STALE 过期记忆。
        "state": m.state.value if getattr(m, "state", None) is not None else None,
    }
    if score is not None:
        d["score"] = score
    return d


@router.get("/memories")
async def list_memories(
    agent_id: str = "",
    session_id: str = "",
    memory_type: str = "",
    scope: str = "",
    query: str = "",
    sort: str = "",
    top_k: int = Query(default=100, le=500, alias="limit"),
) -> list[dict]:
    """List / recall memories.

    Review correction (#4, high): recall routing lives HERE in the route
    layer — it calls ``bus.emit(RECALL)`` (③ RetrieverAgent) and falls back
    to plain ``service.recall`` when no OBSERVER hook is wired. The service
    layer is never reached into from a side agent.

    - ``query``: when non-empty, runs the ③ RetrieverAgent match-scoring
      path (returns ``{..., "score"}`` sorted by score desc). When the
      retriever is disabled (no RECALL hook), falls back to
      ``service.recall(query)``.
    - ``sort=importance``: order the (un-queried) full list by importance
      desc — previously ``GET /memories`` was hardcoded ``query=""`` with no
      ordering, leaving the internal recall capability unexposed.
    - ``top_k`` (alias ``limit``): result cap.
    """
    if query:
        # Route through the ③ RetrieverAgent hook (OBSERVER). Returns None
        # when the feature gate is off → fall back to plain service.recall.
        ctx = RecallContext(
            query=query, agent_id=agent_id, session_id=session_id, scope=scope, top_k=top_k,
        )
        ranked = await _state.memory_event_bus.emit(EventType.RECALL, ctx)
        if ranked is not None:
            return [
                _mem_to_dict(r["item"], score=r["score"]) for r in ranked
            ]
        items = await _state.memory_service.recall(
            query=query,
            agent_id=agent_id,
            session_id=session_id,
            memory_type=MemoryType(memory_type) if memory_type else None,
            scope=MemoryScope(scope) if scope else None,
            top_k=top_k,
        )
        return [_mem_to_dict(m) for m in items]

    items = await _state.memory_service.recall(
        query="",
        agent_id=agent_id,
        session_id=session_id,
        memory_type=MemoryType(memory_type) if memory_type else None,
        scope=MemoryScope(scope) if scope else None,
        top_k=top_k,
    )
    if sort == "importance":
        items = sorted(items, key=lambda m: m.importance, reverse=True)
    return [_mem_to_dict(m) for m in items]


@router.get("/memory/graph")
async def memory_graph(
    entities: str = "",
    agent_id: str = "",
    scope: str = "",
    top_k: int = Query(default=8, ge=1, le=50),
) -> dict:
    """图召回端点(阶段1,spec §2):组装 LIF + KG + 蝴蝶翼图结构返回。

    只读消费内部已算的中间结果,**不新增打分/扩散逻辑**(红线):
      - match/composite ← RetrieverAgent.retrieve(detail=True)
      - lif_activation ← NeuralState.field[concept]
      - wing ← ButterflyRecallStrategy 过滤结果(forward/backward/none)
      - edges.kg_relation ← KnowledgeGraph.get_entity_relations
      - edges.lif_spread ← _kg_neighbors 扩散邻居
      - edges.butterfly_assoc ← ButterflyWing 双向联想(forward/backward)
      - activated_path ← KG 实体 + 召回 memory 激活序列
      - origin/state ← _mem_to_dict(d7823d7 已透出)

    降级:schema 透出 + 空节点(节点来源各组件均 feature-gated,未启用时返回
    ``total_nodes=0`` 的合法图骨架,而非 500)。
    """
    query_entities = [e.strip() for e in (entities or "").split(",") if e.strip()]
    # scope 透传到 RetrieverAgent.retrieve → service.recall 候选层过滤(d7823d7)。
    query = " ".join(query_entities)

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    activated_path: list[str] = []
    lif_snapshot_id: str | None = None

    # ── 神经场态(LIF 激活值 + 快照)─────────────────────────────────
    field: dict[str, float] = {}
    neural_state: Any = None
    if _state.neural_store is not None and agent_id:
        try:
            neural_state = _state.neural_store.load_state(agent_id)
        except Exception:
            neural_state = None
        if neural_state is not None:
            field = dict(getattr(neural_state, "field", {}) or {})
            try:
                lif_snapshot_id = _state.neural_store.latest_stable_snapshot_id(agent_id)
            except Exception:
                lif_snapshot_id = None

    kg = _state.knowledge_graph

    # ── 召回 ranked items(match × lif,含明细)─────────────────────
    ranked: list[dict[str, Any]] = []
    if _state.retriever is not None and (query_entities or query):
        try:
            ranked = await _state.retriever.retrieve(
                query=query,
                agent_id=agent_id,
                top_k=top_k,
                lif_state=neural_state,
                scope=scope,
                detail=True,
            )
        except Exception:
            ranked = []

    query_activated_entities: list[str] = (
        ranked[0].get("activated_entities", []) if ranked else []
    )

    # ── 激活阈值过滤(spec §6 风险2:防大 KG 膨胀)──────────────────
    LIF_THRESHOLD = 0.05

    memory_node_ids: dict[str, str] = {}  # memory_id -> node_id
    entity_node_ids: dict[str, str] = {}  # entity_name -> node_id

    def _entity_node_id(name: str) -> str:
        nid = f"ent::{name}"
        entity_node_ids.setdefault(name, nid)
        return entity_node_ids[name]

    # ── entity 节点(query 命中的 KG 实体 + 场中高电位概念)────────
    candidate_entities: list[str] = []
    for name in query_activated_entities:
        if name and name not in candidate_entities:
            candidate_entities.append(name)
    # 场中电位 > 阈值的概念也作为 entity 节点(神经场投影)。
    if field:
        for concept, pot in sorted(field.items(), key=lambda kv: kv[1], reverse=True):
            if pot > LIF_THRESHOLD and concept and concept not in candidate_entities:
                candidate_entities.append(concept)

    butterfly_store = _get_butterfly_store()

    for name in candidate_entities:
        lif_act = float(field.get(name, 0.0))
        if lif_act <= 0.0 and name not in query_activated_entities:
            continue
        node_id = _entity_node_id(name)
        # 关联回 source memory(KG 反查),kind=entity 时 memory_id 可空。
        memory_id = _entity_source_memory_id(kg, name)
        wing = _wing_for_entity(butterfly_store, memory_id) if memory_id else "none"
        nodes.append({
            "id": node_id,
            "kind": "entity",
            "content": name,
            "memory_id": memory_id,
            "lif_activation": round(lif_act, 4),
            "match_score": 0.0,
            "composite_score": 0.0,
            "origin": None,
            "state": None,
            "scope": scope or None,
            "wing": wing,
        })
        activated_path.append(name)

    # ── memory 节点(ranked items)+ composite_score = match × lif ─
    for r in ranked:
        item = r.get("item")
        if item is None:
            continue
        match = float(r.get("match_score", 0.0))
        lif_w = float(r.get("lif_weight", 1.0))
        composite = float(r.get("score", match * lif_w))
        node_id = f"mem::{item.id}"
        memory_node_ids[item.id] = node_id
        # 该 memory 涉及概念在场中的最高电位(节点 lif_activation)。
        lif_act = _item_lif_activation(item, field)
        wing = _wing_for_memory(butterfly_store, item.id)
        nodes.append({
            "id": node_id,
            "kind": "memory",
            "content": item.content,
            "memory_id": item.id,
            "lif_activation": round(lif_act, 4),
            "match_score": round(match, 4),
            "composite_score": round(composite, 4),
            "origin": getattr(item.origin, "value", None),
            "state": getattr(getattr(item, "state", None), "value", None),
            "scope": getattr(item.scope, "value", None),
            "wing": wing,
        })
        activated_path.append(node_id)

    # ── edges ─────────────────────────────────────────────────────
    # (1) kg_relation:query/场中 entity 之间的 KG 关系(结构边)。
    seen_edges: set[tuple[str, str, str]] = set()

    def _add_edge(src: str, dst: str, rel: str, weight: float, label: str = "") -> None:
        key = (src, dst, rel)
        if key in seen_edges or src == dst:
            return
        seen_edges.add(key)
        edge: dict[str, Any] = {"src": src, "dst": dst, "rel": rel, "weight": round(float(weight), 4)}
        if label:
            edge["label"] = label
        edges.append(edge)

    for name in list(entity_node_ids.keys()):
        relations = _entity_relations(kg, name)
        for rel in relations:
            other = rel.get("other_name")
            if not other:
                continue
            # 只在两端至少一端入了图(entity 节点或场中概念)时连边,防膨胀。
            if other not in entity_node_ids and other not in field:
                continue
            _add_edge(
                _entity_node_id(name),
                _entity_node_id(other),
                "kg_relation",
                float(rel.get("confidence", 0.5)),
                label=str(rel.get("relation_type", "") or ""),
            )

    # (2) lif_spread:神经场扩散沿 KG 邻居的注入边(复用 _kg_neighbors,只读)。
    try:
        from src.memory.neural_field import _kg_neighbors
    except Exception:  # pragma: no cover - defensive import
        _kg_neighbors = None  # type: ignore[assignment]
    if _kg_neighbors is not None and kg is not None:
        for name in list(entity_node_ids.keys()):
            try:
                neighbors = _kg_neighbors(kg, name)
            except Exception:
                neighbors = []
            for neighbor_name, w in neighbors:
                if neighbor_name not in entity_node_ids and neighbor_name not in field:
                    continue
                _add_edge(
                    _entity_node_id(name),
                    _entity_node_id(neighbor_name),
                    "lif_spread",
                    float(w),
                )

    # (3) butterfly_assoc:蝴蝶翼双向联想(forward/backward 关联)。
    for mid, m_node_id in memory_node_ids.items():
        assoc = _butterfly_associations(butterfly_store, mid)
        for other_mid, wing_type, strength in assoc:
            if other_mid not in memory_node_ids:
                continue
            _add_edge(
                m_node_id,
                memory_node_ids[other_mid],
                "butterfly_assoc",
                float(strength),
                label=wing_type,
            )

    return {
        "query_entities": query_entities,
        "nodes": nodes,
        "edges": edges,
        "activated_path": activated_path,
        "meta": {
            "agent_id": agent_id,
            "scope": scope,
            "lif_snapshot_id": lif_snapshot_id,
            "total_nodes": len(nodes),
        },
    }


# ── graph assembly helpers (只读消费,不含打分/扩散) ───────────────


def _get_butterfly_store() -> Any:
    """只读获取已实例化的全局 ButterflyStore(未创建则 None,不主动实例化)。

    ButterflyStore 是 lazy 单例(``butterfly_wing.get_default_store``);此处只读消费
    它的 ``_default_store`` 模块属性,避免在图端点副作用地创建一个新的 SQLite 文件
    (保持图端点对持久化的零副作用,红线:图组装只读)。
    """
    try:
        from src.memory import butterfly_wing
    except Exception:
        return None
    return getattr(butterfly_wing, "_default_store", None)


def _entity_source_memory_id(kg: Any, name: str) -> str:
    """KG 反查 entity 关联的首个 source_memory_id(无则空串)。"""
    if kg is None or not name:
        return ""
    try:
        eid = kg._resolve_entity_id(name)
    except Exception:
        return ""
    if not eid:
        return ""
    try:
        ent = kg.get_entity(eid)
    except Exception:
        return ""
    if not ent:
        return ""
    src = ent.get("source_memory_ids") or []
    return src[0] if src else ""


def _entity_relations(kg: Any, name: str) -> list[dict[str, Any]]:
    """取 entity 的 KG 关系边,规范化为 {other_name, relation_type, confidence}。

    无向看待(source/target 两侧都算邻居),与 neural_field._kg_neighbors 一致。
    """
    if kg is None or not name:
        return []
    try:
        eid = kg._resolve_entity_id(name)
    except Exception:
        return []
    if not eid:
        return []
    try:
        rels = kg.get_entity_relations(eid)
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for r in rels or []:
        src = r.get("source_id")
        tgt = r.get("target_id")
        other_id = tgt if src == eid else src
        if not other_id or other_id == eid:
            continue
        try:
            ent = kg.get_entity(other_id)
        except Exception:
            ent = None
        other_name = ent.get("name") if ent else None
        if not other_name:
            continue
        out.append({
            "other_name": other_name,
            "relation_type": r.get("relation_type", "") or "",
            "confidence": float(r.get("confidence") or 0.5),
        })
    return out


def _item_lif_activation(item: Any, field: dict[str, float]) -> float:
    """memory item 在场中涉及概念的最高电位(best-effort,只读)。"""
    if not field:
        return 0.0
    content_lower = (getattr(item, "content", "") or "").lower()
    matched = [pot for concept, pot in field.items() if concept and concept.lower() in content_lower]
    if not matched:
        return 0.0
    return float(max(matched))


def _wing_for_entity(butterfly_store: Any, memory_id: str) -> str:
    """entity 关联 memory 的蝴蝶翼激活类型(forward/backward/none)。"""
    if not memory_id:
        return "none"
    return _wing_for_memory(butterfly_store, memory_id)


def _wing_for_memory(butterfly_store: Any, memory_id: str) -> str:
    """memory 的蝴蝶翼激活类型(forward/backward/none,只读查 ButterflyStore)。"""
    if butterfly_store is None or not memory_id:
        return "none"
    try:
        wing = butterfly_store.load_wing(memory_id)
    except Exception:
        return "none"
    if wing is None:
        return "none"
    fwd = getattr(wing, "forward_score", 0.0) or 0.0
    bwd = getattr(wing, "backward_score", 0.0) or 0.0
    if fwd > 0.5 and bwd > 0.5:
        return "forward"  # 双激活归到 forward(归纳主导)
    if fwd > 0.5:
        return "forward"
    if bwd > 0.5:
        return "backward"
    return "none"


def _butterfly_associations(butterfly_store: Any, memory_id: str) -> list[tuple[str, str, float]]:
    """memory 的蝴蝶翼双向联想邻居 [(other_memory_id, wing_type, strength)]。

    只读查 ButterflyWing.forward_associations / backward_associations(实体/memory id 列表)。
    """
    if butterfly_store is None or not memory_id:
        return []
    try:
        wing = butterfly_store.load_wing(memory_id)
    except Exception:
        return []
    if wing is None:
        return []
    out: list[tuple[str, str, float]] = []
    for assoc in (getattr(wing, "forward_associations", []) or []):
        if assoc:
            out.append((str(assoc), "forward", float(getattr(wing, "forward_score", 0.0) or 0.0)))
    for assoc in (getattr(wing, "backward_associations", []) or []):
        if assoc:
            out.append((str(assoc), "backward", float(getattr(wing, "backward_score", 0.0) or 0.0)))
    return out


@router.get("/identity")
async def identity(agent_id: str = "") -> dict:
    """Identity-recall closure (Chapter 6.2).

    Returns four memory groups keyed by ``identity_category`` plus the neural
    field's ``attention_group`` and ``baseline`` (personality, slow-varying).
    Programmatic only — NO LLM synthesis here. Per design §6.3, emergent
    self-narration is the REQUESTER's concern; this endpoint only delivers
    the memory groups + score. The four buckets:

      - what_i_remember: attention_group + KNOWLEDGE-tagged top
      - who_am_i:        IDENTITY-tagged memories
      - my_goals:        GOAL-tagged memories
      - my_traits:       TRAIT-tagged memories
      - personality:     neural baseline (LIF slow-varying sediment)
    """
    if not agent_id:
        agent_id = next(iter(_state.agents.keys()), "")

    out: dict[str, Any] = {
        "agent_id": agent_id,
        "what_i_remember": [],
        "who_am_i": [],
        "my_goals": [],
        "my_traits": [],
        "personality": {},
    }

    # identity_category is stored in item metadata by ① IngestorAgent.
    # Bucket the agent's non-archived memories by their tag.
    try:
        items = await _state.memory_service.recall(
            query="", agent_id=agent_id, top_k=500
        )
    except Exception:
        items = []

    buckets = {
        "IDENTITY": "who_am_i",
        "GOAL": "my_goals",
        "TRAIT": "my_traits",
        "KNOWLEDGE": "what_i_remember",
    }
    knowledge_items: list[Any] = []
    for m in items:
        if getattr(m, "archived", False):
            continue
        meta = getattr(m, "metadata", {}) or {}
        cat = str(meta.get("identity_category", "NONE")).upper()
        target_key = buckets.get(cat)
        if target_key is None:
            continue
        out[target_key].append(_mem_to_dict(m))
        if cat == "KNOWLEDGE":
            knowledge_items.append(m)

    # what_i_remember = attention_group (neural field projection) + KNOWLEDGE.
    attention_ids: list[str] = []
    if _state.neural_store is not None:
        try:
            state = _state.neural_store.load_state(agent_id)
            if state is not None:
                attention_ids = list(state.attention_group)
                out["personality"] = dict(state.baseline)
        except Exception:
            pass

    if attention_ids:
        seen = {m["id"] for m in out["what_i_remember"]}
        for mid in attention_ids:
            if mid in seen:
                continue
            item = await _state.memory_service.get(mid)
            if item is not None and not getattr(item, "archived", False):
                out["what_i_remember"].insert(0, _mem_to_dict(item))

    return out


@router.get("/memories/layers")
async def memory_layers(agent_id: str = "") -> dict:
    """Get memory layer statistics for an agent."""
    items = await _state.memory_service.recall(
        query="",
        agent_id=agent_id,
        top_k=1000,
    )
    stats: dict[str, int] = {"working": 0, "session": 0, "episodic": 0, "semantic": 0}
    for m in items:
        if not m.archived and m.memory_type.value in stats:
            stats[m.memory_type.value] += 1
    return stats


@router.delete("/memories/{memory_id}")
async def delete_memory(memory_id: str) -> dict:
    """Delete a memory item."""
    deleted = await _state.memory_service.delete(memory_id)
    return {"deleted": deleted}


# ── External Maintenance API ───────────────────────────────────────


@router.post("/memory/notify")
async def notify_maintenance(req: MemoryNotifyRequest) -> dict:
    """Trigger deterministic maintenance (zero LLM) on demand.

    For external apps sharing the memories DB: call this after writing to the
    ``memories`` table to immediately run prune → forget → migrate (the SAME
    chain as the 60s poll). Only touches ``origin=AGENT`` memories; FOREGROUND
    (user / external-app-authored) memories are P0-protected and never
    auto-touched. Set ``force=True`` to run unconditionally (bypasses the
    updated_at change check).
    """
    if _state.db_watcher is None:
        return {"status": "unavailable", "results": []}
    agent_ids = [req.agent_id] if req.agent_id else list(_state.agents.keys())
    results: list[dict] = []
    for aid in agent_ids:
        try:
            if req.force:
                run = True
            else:
                changed = await asyncio.to_thread(_state.db_watcher.has_external_changes)
                run = changed is not None
            if run:
                r = await _state.db_watcher.run_maintenance(agent_id=aid, emit=True, trigger="notify")
                results.append(r)
            else:
                results.append({"agent_id": aid, "status": "noop", "reason": "no_external_changes"})
        except Exception as exc:
            results.append({"agent_id": aid, "status": "error", "error": str(exc)})
    processed = sum(1 for r in results if r.get("status") == "ok")

    # ?curate=true → fire a ④ CuratorAgent pass as an INDEPENDENT
    # fire-and-forget task. NEVER inserted into the synchronous
    # run_maintenance Zero-LLM chain above (review correction #11).
    curate_triggered = False
    if req.curate and _state.curator is not None:
        agent_ids = [req.agent_id] if req.agent_id else list(_state.agents.keys())
        for aid in agent_ids:
            ctx = CurateContext(agent_id=aid, scope="all")
            asyncio.create_task(_state.memory_event_bus.emit(EventType.CURATE, ctx))
        curate_triggered = bool(agent_ids)

    return {
        "status": "ok",
        "processed": processed,
        "results": results,
        "curate_triggered": curate_triggered,
    }


@router.post("/memory/consolidate")
async def consolidate_memory(req: MemoryConsolidateRequest) -> dict:
    """Trigger consolidation on demand.

    Two modes:
    - ``mode=merge`` (default-agnostic): trigger ② ConsolidatorAgent —
      episodic → semantic understanding-driven merge (LLM), distinct from
      the task-post experience sedimentation below.
    - default (``task_consolidator``): reuses TaskConsolidationAgent —
      extracts key decisions / pitfalls from ``messages`` and writes them
      via BackwardWriter. Timeout clamped to 8s; degrades to heuristic
      EPISODIC summary on LLM failure. Rejects empty ``messages``.
    """
    if req.mode == "merge":
        if _state.consolidator is None:
            return {"status": "unavailable", "reason": "consolidator_disabled"}
        agent_id = req.agent_id or next(iter(_state.agents.keys()), "")
        try:
            result = await _state.consolidator.consolidate(
                agent_id=agent_id,
                trigger="api_merge",
                top_k=20,
                timeout=min(float(req.timeout or 8.0), 8.0),
            )
            return {
                "triggered": result.triggered,
                "degraded": getattr(result, "degraded", False),
                "written": getattr(result, "written", False),
                "merged_count": getattr(result, "merged_count", 0),
                "semantic_ids": getattr(result, "semantic_ids", []),
                "archived_ids": getattr(result, "archived_ids", []),
                "error": getattr(result, "error", "") or None,
            }
        except Exception as exc:
            return {"status": "error", "error": str(exc)}

    if _state.task_consolidator is None:
        return {"status": "unavailable"}
    if not req.messages:
        return {"triggered": False, "written": False, "error": "messages_required"}
    llm = _state.llm_client
    if llm is not None and not getattr(llm, "api_key", None) and not getattr(llm, "anthropic_api_key", None):
        return {"triggered": False, "degraded": True, "written": False, "error": "llm_unconfigured"}
    timeout = min(float(req.timeout or 8.0), 8.0)
    try:
        result = await _state.task_consolidator.consolidate_task(
            agent_id=req.agent_id,
            session_id=req.session_id,
            messages=req.messages,
            timeout=timeout,
        )
        return {
            "triggered": result.triggered,
            "degraded": getattr(result, "degraded", False),
            "confidence": result.confidence,
            "written": result.written,
            "memory_id": result.memory_id,
            "channel": result.channel,
            "error": getattr(result, "error", None) or None,
        }
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


# ── Permission API ─────────────────────────────────────────────────


@router.post("/permissions/grant")
async def grant_permission(req: GrantPermissionRequest) -> dict:
    """Grant memory access permission between agents."""
    grant_id = _state.memory_service.grant_access(
        grantor_id=req.grantor_id,
        grantee_id=req.grantee_id,
        target_agent_id=req.target_agent_id,
        level=req.level,
        expires_at=req.expires_at,
    )
    return {"grant_id": grant_id, "level": req.level}


@router.get("/permissions/log")
async def permission_log(
    accessor_id: str = "",
    target_agent_id: str = "",
    limit: int = Query(default=50, le=500),
) -> list[dict]:
    """Get memory access log."""
    entries = _state.memory_service.get_access_log(
        accessor_id=accessor_id,
        target_agent_id=target_agent_id,
        limit=limit,
    )
    return [
        {
            "id": e.id,
            "accessor_id": e.accessor_id,
            "target_agent_id": e.target_agent_id,
            "memory_id": e.memory_id,
            "action": e.action,
            "level_granted": e.level_granted.value,
            "granted_at": e.granted_at,
        }
        for e in entries
    ]


# ── Communication API ─────────────────────────────────────────────


@router.post("/messages")
async def send_message(req: SendMessageRequest) -> dict:
    """Send a message between agents."""
    msg = AgentMessage(
        sender_id=req.sender_id,
        recipient_id=req.recipient_id,
        session_id=req.session_id,
        workspace_id=req.workspace_id,
        content=req.content,
        message_type=MessageType(req.message_type),
        priority=MessagePriority(req.priority),
    )

    if req.recipient_id:
        msg_id = await _state.communication_bus.send(msg)
    else:
        ids = await _state.communication_bus.broadcast(msg, session_id=req.session_id)
        return {"broadcast": True, "delivered_ids": ids, "count": len(ids)}

    return {"id": msg_id, "status": "delivered"}


@router.get("/messages")
async def list_messages(
    agent_id: str = "",
    session_id: str = "",
    limit: int = Query(default=50, le=500),
) -> list[dict]:
    """Get message history for an agent or session."""
    if agent_id:
        history = _state.communication_bus.get_history(agent_id, limit=limit)
        return [m.to_dict() for m in history]
    return []
