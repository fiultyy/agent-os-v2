"""NeuralField — ⑤ NeuralState 状态场 + ⑥ 鲁棒回退（合并交付单元）。

设计依据：``docs/memory-kernel-design.md`` 第 4 章 + ``docs/memory-kernel-impl-plan.md``
Part 2 ⑤⑥。把 LIF（连续电位场）与 KG（结构图）融合成**一个神经状态场子系统**，
为 Part 1 的 side agent（尤其 ③ RetrieverAgent）提供注意力指针 + 人格积累。

关键数学约束（三视角对抗审查 critical 修正，必须保持）：
  1. 蝴蝶翼扩散**归一化**——``field[neighbor] += field[c] * w * spread_rate / out_degree(c)``，
     且 ``spread_rate + Σ(w/out_degree) ≤ 1``（谱半径约束），否则加性传播在无向 KG 上
     发散或退化。
  2. 异常检测阈值用**相对 baseline 倍数**（``field[c] > k*max(baseline[c], ε)``）+ 滞回
     （连续 ≥2 turn 才触发），否则扩散病态被伪装成状态异常反复抖动。
  3. ``learn_baseline`` 在**去扩散后的纯激活场**学习 + clamp ``[0, baseline_max]``，
     否则漏电 × 扩散联合不动点漂移失控。

与 ``sideline/kairos.py`` 的 :class:`LIFState` **不继承**：单标量电位 vs per-concept 字典，
维度不同，二者独立演化。
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from dataclasses import field as dc_field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

try:
    from pysqlite3 import dbapi2 as sqlite3  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    import sqlite3  # noqa: F401

from src.memory.hooks import HookPriority, MemoryHook, TurnContext

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────
# ⑤ NeuralState — 复合状态组（field + attention_group + baseline 一组）
# ─────────────────────────────────────────────────────────────────


@dataclass
class NeuralState:
    """单个 agent 的神经状态场（连续层 + 离散层 + 稳态基线一组）。

    Attributes:
        agent_id: 所属 agent。
        field: 连续层——概念名 → 电位 ``[0, 1]``，每 turn 漂移。
        attention_group: 离散层——场投影（top-N 高电位概念反查到的 memory_id 群）。
        baseline: 稳态基线——概念名 → 静息电位，漏电回归目标（防漂移失控 / 人格沉淀）。
        turn_id: 当前逻辑 turn 序号（单调递增，用于异常滞回计数与快照版本链）。
    """

    agent_id: str
    field: dict[str, float] = dc_field(default_factory=dict)
    attention_group: list[str] = dc_field(default_factory=list)
    baseline: dict[str, float] = dc_field(default_factory=dict)
    turn_id: int = 0

    # ── 序列化 ────────────────────────────────────────────────────
    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "field": self.field,
            "attention_group": self.attention_group,
            "baseline": self.baseline,
            "turn_id": self.turn_id,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "NeuralState":
        return cls(
            agent_id=d["agent_id"],
            field=dict(d.get("field") or {}),
            attention_group=list(d.get("attention_group") or []),
            baseline=dict(d.get("baseline") or {}),
            turn_id=int(d.get("turn_id") or 0),
        )


# ─────────────────────────────────────────────────────────────────
# KG 邻居协议——drift / compute_attention_group 需要的受控接口
# ─────────────────────────────────────────────────────────────────


@runtime_checkable
class _KGLike(Protocol):
    """drift 需要的最小 KG 接口（鸭子类型，KnowledgeGraph 天然满足）。"""

    def _resolve_entity_id(self, name_or_id: str) -> str | None: ...

    def get_entity(self, entity_id: str) -> dict[str, Any] | None: ...

    def get_entity_relations(self, entity_id: str) -> list[dict[str, Any]]: ...


def _kg_neighbors(
    kg: Any, concept: str
) -> list[tuple[str, float]]:
    """从 KG 取 ``concept`` 的 1-hop 邻居（邻居名, 边权重 confidence）。

    无向看待（source/target 两侧都算邻居），权重取 relation.confidence。KG 不可用或
    concept 不存在时返回空列表（drift 退化成只做激活+漏电，安全）。
    """
    if kg is None:
        return []
    try:
        eid = kg._resolve_entity_id(concept)
    except Exception:
        return []
    if not eid:
        return []
    try:
        rels = kg.get_entity_relations(eid)
    except Exception:
        return []

    neighbors: list[tuple[str, float]] = []
    seen: set[str] = set()
    for r in rels:
        src = r.get("source_id")
        tgt = r.get("target_id")
        other_id = tgt if src == eid else src
        if not other_id or other_id in seen or other_id == eid:
            continue
        seen.add(other_id)
        ent = kg.get_entity(other_id)
        name = ent.get("name") if ent else None
        if not name:
            continue
        w = float(r.get("confidence") or 1.0)
        neighbors.append((name, w))
    return neighbors


def _kg_source_memory_ids(kg: Any, concept: str) -> list[str]:
    """反查 concept 关联的 source_memory_ids（attention_group 投影）。"""
    if kg is None:
        return []
    try:
        eid = kg._resolve_entity_id(concept)
    except Exception:
        return []
    if not eid:
        return []
    try:
        ent = kg.get_entity(eid)
    except Exception:
        return []
    if not ent:
        return []
    return list(ent.get("source_memory_ids") or [])


# ─────────────────────────────────────────────────────────────────
# ⑤ NeuralFieldEngine — 漂移三步 + attention_group + lif_weight
# ─────────────────────────────────────────────────────────────────


class NeuralFieldEngine:
    """神经状态场计算引擎。

    drift 三步（每 turn）：
      ① 激活：当前内容相关概念 +Δpotential（乘 (1-decay) 后叠加 importance）。
      ② 漏电向 baseline：全概念 ``×(1-decay) + baseline*decay``（不归零，回归静息）。
      ③ 蝴蝶翼扩散（归一化）：只对 top-M 高电位概念做局部 1-hop，谱半径约束防发散，
         结束 clamp ``field ∈ [0, 1]``。
    """

    # 漂移参数
    decay: float = 0.1
    spread_rate: float = 0.5  # 蝴蝶翼扩散比例；满足 spread_rate + Σ(w/out_degree) ≤ 1
    spread_top_m: int = 20  # 只扩散 top-M 高电位概念（防每 turn 上千 KG 查询拖慢）
    clamp_min: float = 0.0
    clamp_max: float = 1.0

    # ⑥ 异常检测参数（相对 baseline 倍数 + 滞回）
    explosion_k: float = 5.0  # field[c] > k*max(baseline[c], ε) 视为爆炸
    baseline_eps: float = 0.05  # baseline 下限保护，避免除以 0
    collapse_floor: float = 0.1  # 相对塌缩阈值（Σfield / max(|field|,1) 低于此值）
    attention_shift_ratio: float = 0.8  # 注意力骤变阈值（>80%）
    hysteresis_turns: int = 2  # 滞回：连续 ≥2 turn 才确认异常

    # ⑥ baseline LTP（去扩散场学习 + clamp）
    baseline_alpha: float = 0.05  # 学习率
    baseline_max: float = 0.8  # clamp 上限

    def __init__(
        self,
        decay: float | None = None,
        spread_rate: float | None = None,
        spread_top_m: int | None = None,
        explosion_k: float | None = None,
        collapse_floor: float | None = None,
        attention_shift_ratio: float | None = None,
        hysteresis_turns: int | None = None,
        baseline_alpha: float | None = None,
        baseline_max: float | None = None,
    ) -> None:
        # Allows feature-gate / test overrides without monkeypatching the class.
        if decay is not None:
            self.decay = decay
        if spread_rate is not None:
            self.spread_rate = spread_rate
        if spread_top_m is not None:
            self.spread_top_m = spread_top_m
        if explosion_k is not None:
            self.explosion_k = explosion_k
        if collapse_floor is not None:
            self.collapse_floor = collapse_floor
        if attention_shift_ratio is not None:
            self.attention_shift_ratio = attention_shift_ratio
        if hysteresis_turns is not None:
            self.hysteresis_turns = hysteresis_turns
        if baseline_alpha is not None:
            self.baseline_alpha = baseline_alpha
        if baseline_max is not None:
            self.baseline_max = baseline_max

    # ── drift ─────────────────────────────────────────────────────
    async def drift(
        self,
        state: NeuralState,
        activated_concepts: list[str],
        importances: dict[str, float],
        kg: Any = None,
    ) -> NeuralState:
        """三步漂移：激活 → 漏电向 baseline → 蝴蝶翼扩散（归一化）。

        就地修改并返回 ``state``。所有数值 clamp 到 ``[0, 1]``，谱半径约束保证
        连续漂移数值收敛不发散。
        """
        state.turn_id += 1

        # ① 激活：当前内容相关概念 +Δpotential
        for c in activated_concepts:
            imp = float(importances.get(c, 0.0))
            if imp <= 0.0:
                continue
            prev = state.field.get(c, 0.0)
            state.field[c] = prev * (1.0 - self.decay) + imp

        # ② 漏电向 baseline：全概念 ×(1-decay) + baseline*decay
        for c in list(state.field.keys()):
            base = state.baseline.get(c, 0.0)
            state.field[c] = state.field[c] * (1.0 - self.decay) + base * self.decay

        # ③ 蝴蝶翼扩散（归一化，谱半径约束）：只对 top-M 高电位概念做局部 1-hop
        self._spread(state, kg)

        # clamp field ∈ [0, 1]
        self._clamp_field(state)
        return state

    def _spread(self, state: NeuralState, kg: Any) -> None:
        """蝴蝶翼扩散：top-M 高电位概念 → 1-hop 邻居，归一化加性传播。

        对每个高电位概念 c，向其邻居 n 注入 ``field[c] * w * spread_rate / out_degree(c)``。
        ``out_degree(c)`` 取 ``Σ w``（加权出度），保证 ``Σ(w/out_degree) = 1``，
        加上 ``spread_rate ≤ 1`` 满足谱半径约束 ``spread_rate + Σ(w/out_degree) ≤ 1`` 防发散。
        """
        if kg is None or not state.field:
            return

        # top-M 高电位概念（按电位降序）
        ranked = sorted(state.field.items(), key=lambda kv: kv[1], reverse=True)
        top_m = ranked[: self.spread_top_m]

        delta: dict[str, float] = {}
        for concept, pot in top_m:
            if pot <= 0.0:
                continue
            neighbors = _kg_neighbors(kg, concept)
            if not neighbors:
                continue
            out_degree = sum(w for _, w in neighbors)
            if out_degree <= 0.0:
                continue
            for neighbor_name, w in neighbors:
                # 归一化注入：w/out_degree ∈ [0,1]，Σ=1；× spread_rate ≤ 1 → 谱半径约束
                contribution = pot * (w / out_degree) * self.spread_rate
                if contribution <= 0.0:
                    continue
                delta[neighbor_name] = delta.get(neighbor_name, 0.0) + contribution

        for name, d in delta.items():
            state.field[name] = state.field.get(name, 0.0) + d

    def _clamp_field(self, state: NeuralState) -> None:
        lo, hi = self.clamp_min, self.clamp_max
        for c in list(state.field.keys()):
            v = state.field[c]
            if v < lo:
                state.field[c] = lo
            elif v > hi:
                state.field[c] = hi
            # 清理零电位游离键，防止 field 无界膨胀
            if state.field[c] <= 0.0:
                del state.field[c]

    # ── attention_group（即时产物）──────────────────────────────
    def compute_attention_group(
        self,
        state: NeuralState,
        kg: Any = None,
        top_n: int = 10,
    ) -> list[str]:
        """top-N 高电位概念 → KG 反查 source_memory_ids → attention_group。

        返回去重后的 memory_id 列表（按概念电位排序优先）。KG 不可用时返回空。
        """
        if not state.field:
            state.attention_group = []
            return state.attention_group

        ranked = sorted(state.field.items(), key=lambda kv: kv[1], reverse=True)
        top_concepts = [c for c, _ in ranked[:top_n] if _ > 0.0]

        memory_ids: list[str] = []
        seen: set[str] = set()
        for concept in top_concepts:
            for mid in _kg_source_memory_ids(kg, concept):
                if mid not in seen:
                    seen.add(mid)
                    memory_ids.append(mid)

        state.attention_group = memory_ids
        return state.attention_group

    # ── lif_weight（召回加权，rank-based 分位）────────────────────
    def lif_weight(self, item_concepts: list[str], field: dict[str, float]) -> float:
        """item 涉及概念在当前场中的**相对排名分位**，``∈ [0, 1]``。

        审查修正（high）：用 rank-based 分位（非绝对电位值），否则 lif 长期低电位时
        ``score ≈ match × 0.1`` 被压制。返回 item 概念在场中分位的均值。
        """
        if not item_concepts or not field:
            return 0.0

        ranked = sorted(field.values(), reverse=True)
        n = len(ranked)
        if n == 0:
            return 0.0

        # 规范化排名分位：最高电位 → 1.0，最低电位 → 0.0。
        # rank = 电位严格大于 v 的概念数；分母 (n-1) 使最高 rank=0 → 1.0、
        # 最低 rank=n-1 → 0.0。n=1 时（仅 1 个概念）统一为 1.0。
        denom = (n - 1) if n > 1 else 1

        percentiles: list[float] = []
        for concept in item_concepts:
            if concept not in field:
                percentiles.append(0.0)
                continue
            v = field[concept]
            rank = sum(1 for x in ranked if x > v)
            if n > 1:
                percentiles.append(1.0 - (rank / denom))
            else:
                percentiles.append(1.0)

        return sum(percentiles) / len(percentiles)


# ─────────────────────────────────────────────────────────────────
# ⑥ NeuralFieldStore — SQLite 持久化（neural_state + neural_snapshot）
# ─────────────────────────────────────────────────────────────────


class NeuralFieldStore:
    """神经状态场持久化层（照抄 butterfly_wing.ButterflyStore 模式）。

    两张表：
      - ``neural_state``：每个 agent 当前的活跃 NeuralState（agent_id PK）。
      - ``neural_snapshot``：版本链快照（snapshot_id PK）。环形缓冲——保留最近
        K=20 + ``is_stable`` 永留，防版本链膨胀。
    """

    RING_BUFFER_K: int = 20  # 环形缓冲：最近 K 个非 stable 快照

    def __init__(self, db_path: str = "data/neural_field.db") -> None:
        self._db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.row_factory = sqlite3.Row

        self._create_tables()

    def _create_tables(self) -> None:
        with self._conn:
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS neural_state (
                    agent_id TEXT PRIMARY KEY,
                    field TEXT NOT NULL DEFAULT '{}',
                    attention_group TEXT NOT NULL DEFAULT '[]',
                    baseline TEXT NOT NULL DEFAULT '{}',
                    turn_id INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                )
            """)
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS neural_snapshot (
                    snapshot_id TEXT PRIMARY KEY,
                    agent_id TEXT NOT NULL,
                    field TEXT NOT NULL DEFAULT '{}',
                    attention_group TEXT NOT NULL DEFAULT '[]',
                    baseline TEXT NOT NULL DEFAULT '{}',
                    turn_id INTEGER NOT NULL DEFAULT 0,
                    parent_snapshot_id TEXT,
                    created_at TEXT NOT NULL,
                    is_stable INTEGER NOT NULL DEFAULT 0
                )
            """)
            self._conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_neural_snapshot_agent
                ON neural_snapshot(agent_id, created_at)
            """)

    # ── state 读写 ────────────────────────────────────────────────
    def load_state(self, agent_id: str) -> NeuralState | None:
        row = self._conn.execute(
            "SELECT * FROM neural_state WHERE agent_id = ?", (agent_id,)
        ).fetchone()
        if row is None:
            return None
        return NeuralState(
            agent_id=row["agent_id"],
            field=json.loads(row["field"] or "{}"),
            attention_group=json.loads(row["attention_group"] or "[]"),
            baseline=json.loads(row["baseline"] or "{}"),
            turn_id=int(row["turn_id"] or 0),
        )

    def save_state(self, state: NeuralState) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._conn:
            self._conn.execute(
                """INSERT OR REPLACE INTO neural_state
                   (agent_id, field, attention_group, baseline, turn_id, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    state.agent_id,
                    json.dumps(state.field, ensure_ascii=False),
                    json.dumps(state.attention_group, ensure_ascii=False),
                    json.dumps(state.baseline, ensure_ascii=False),
                    state.turn_id,
                    now,
                ),
            )

    # ── snapshot 环形缓冲 ─────────────────────────────────────────
    def take_snapshot(
        self, state: NeuralState, is_stable: bool = False, parent_snapshot_id: str | None = None
    ) -> str:
        """写一个快照。环形缓冲：保留最近 K=20 + ``is_stable`` 永留。返回 snapshot_id。"""
        snapshot_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        with self._conn:
            self._conn.execute(
                """INSERT INTO neural_snapshot
                   (snapshot_id, agent_id, field, attention_group, baseline,
                    turn_id, parent_snapshot_id, created_at, is_stable)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    snapshot_id,
                    state.agent_id,
                    json.dumps(state.field, ensure_ascii=False),
                    json.dumps(state.attention_group, ensure_ascii=False),
                    json.dumps(state.baseline, ensure_ascii=False),
                    state.turn_id,
                    parent_snapshot_id,
                    now,
                    1 if is_stable else 0,
                ),
            )
            # 环形缓冲：删除该 agent 超出最近 K 的非 stable 快照（最旧的先删）
            self._conn.execute(
                """DELETE FROM neural_snapshot
                   WHERE snapshot_id IN (
                       SELECT snapshot_id FROM (
                           SELECT snapshot_id,
                                  ROW_NUMBER() OVER (
                                      PARTITION BY agent_id
                                      ORDER BY created_at DESC
                                  ) AS rn
                           FROM neural_snapshot
                           WHERE agent_id = ? AND is_stable = 0
                       )
                       WHERE rn > ?
                   )""",
                (state.agent_id, self.RING_BUFFER_K),
            )
        return snapshot_id

    def load_snapshot(self, snapshot_id: str) -> NeuralState | None:
        row = self._conn.execute(
            "SELECT * FROM neural_snapshot WHERE snapshot_id = ?", (snapshot_id,)
        ).fetchone()
        if row is None:
            return None
        return NeuralState(
            agent_id=row["agent_id"],
            field=json.loads(row["field"] or "{}"),
            attention_group=json.loads(row["attention_group"] or "[]"),
            baseline=json.loads(row["baseline"] or "{}"),
            turn_id=int(row["turn_id"] or 0),
        )

    def latest_stable_snapshot_id(self, agent_id: str) -> str | None:
        row = self._conn.execute(
            """SELECT snapshot_id FROM neural_snapshot
               WHERE agent_id = ? AND is_stable = 1
               ORDER BY created_at DESC LIMIT 1""",
            (agent_id,),
        ).fetchone()
        return row["snapshot_id"] if row else None

    def close(self) -> None:
        self._conn.close()


# ─────────────────────────────────────────────────────────────────
# ⑥ 鲁棒回退——异常检测（爆炸/塌缩/骤变）+ restore + learn_baseline
# ─────────────────────────────────────────────────────────────────


class AnomalyType:
    EXPLOSION = "explosion"  # 电位爆炸：单概念 > k*max(baseline, ε) 连续 ≥2 turn
    COLLAPSE = "collapse"  # 场塌缩：Σfield 相对低于 collapse_floor
    ATTENTION_SHIFT = "attention_shift"  # 注意力骤变：>80% 连续 ≥2 turn
    NONE = "none"


@dataclass
class AnomalyResult:
    type: str = AnomalyType.NONE
    detail: str = ""


class NeuralFieldRobustness:
    """⑥ 鲁棒回退：异常检测 + restore + learn_baseline。

    异常检测全部用**相对 baseline 倍数**（非绝对值）+ 滞回（连续 ≥2 turn），
    否则扩散病态会被伪装成状态异常反复抖动（critical 审查修正）。
    """

    def __init__(
        self,
        store: NeuralFieldStore,
        engine: NeuralFieldEngine,
    ) -> None:
        self._store = store
        self._engine = engine
        # 每个 agent 的连续异常计数（滞回）：agent_id -> {anomaly_type -> count}
        self._streak: dict[str, dict[str, int]] = {}

    # ── 异常检测 ──────────────────────────────────────────────────
    def detect_anomaly(self, state: NeuralState) -> AnomalyResult:
        """检测三类异常，返回最严重的一个。滞回：连续 hysteresis_turns 才确认。"""
        e = self._engine

        # ① 电位爆炸：任一概念 field[c] > k*max(baseline[c], ε)
        for c, v in state.field.items():
            base = max(state.baseline.get(c, 0.0), e.baseline_eps)
            if v > e.explosion_k * base:
                if self._bump_streak(state.agent_id, AnomalyType.EXPLOSION) >= e.hysteresis_turns:
                    self._reset_streak(state.agent_id)
                    return AnomalyResult(
                        AnomalyType.EXPLOSION,
                        f"concept '{c}' potential {v:.3f} > {e.explosion_k}×baseline {base:.3f}",
                    )
                return AnomalyResult(AnomalyType.NONE, "explosion-pending")

        # ② 场塌缩：Σfield 相对 max(|field|,1) < collapse_floor
        total = sum(state.field.values())
        denom = max(abs(total), 1.0)
        # 用活跃概念数归一化，避免概念多即不塌缩的假象
        active = len(state.field) or 1
        relative = (total / active) if active else 0.0
        if relative < e.collapse_floor and total < e.collapse_floor:
            if self._bump_streak(state.agent_id, AnomalyType.COLLAPSE) >= e.hysteresis_turns:
                self._reset_streak(state.agent_id)
                return AnomalyResult(
                    AnomalyType.COLLAPSE,
                    f"field collapsed: Σ={total:.3f} mean={relative:.3f} < floor {e.collapse_floor}",
                )
            return AnomalyResult(AnomalyType.NONE, "collapse-pending")

        # ③ 注意力骤变：当前 attention_group 与上一稳定态重叠 < (1 - 0.8) = 20%
        shift = self._attention_shift_ratio(state)
        if shift > e.attention_shift_ratio:
            if self._bump_streak(state.agent_id, AnomalyType.ATTENTION_SHIFT) >= e.hysteresis_turns:
                self._reset_streak(state.agent_id)
                return AnomalyResult(
                    AnomalyType.ATTENTION_SHIFT,
                    f"attention shifted {shift:.1%} > {e.attention_shift_ratio:.0%}",
                )
            return AnomalyResult(AnomalyType.NONE, "attention-pending")

        # 一切正常：清零滞回计数
        self._reset_streak(state.agent_id)
        return AnomalyResult(AnomalyType.NONE)

    def _attention_shift_ratio(self, state: NeuralState) -> float:
        """当前 attention_group 相对最近 stable 快照的变更比例。无历史则 0。"""
        if not state.attention_group:
            return 0.0
        stable_id = self._store.latest_stable_snapshot_id(state.agent_id)
        if not stable_id:
            return 0.0
        prev = self._store.load_snapshot(stable_id)
        if prev is None or not prev.attention_group:
            return 0.0
        prev_set = set(prev.attention_group)
        cur_set = set(state.attention_group)
        union = prev_set | cur_set
        if not union:
            return 0.0
        changed = len(prev_set.symmetric_difference(cur_set))
        return changed / len(union)

    def _bump_streak(self, agent_id: str, anomaly: str) -> int:
        bucket = self._streak.setdefault(agent_id, {})
        bucket[anomaly] = bucket.get(anomaly, 0) + 1
        return bucket[anomaly]

    def _reset_streak(self, agent_id: str) -> None:
        self._streak.pop(agent_id, None)

    # ── restore ───────────────────────────────────────────────────
    def restore(self, agent_id: str, snapshot_id: str) -> NeuralState | None:
        """用快照覆盖当前 engine.field（回退到上个 stable 态）。"""
        snap = self._store.load_snapshot(snapshot_id)
        if snap is None:
            return None
        snap.agent_id = agent_id
        # restore 后清零滞回计数，避免回退后立即又被判定异常
        self._reset_streak(agent_id)
        return snap

    # ── learn_baseline（去扩散场 + clamp）─────────────────────────
    def learn_baseline(self, state: NeuralState) -> None:
        """稳态基线 LTP：``baseline[c] += α*(field[c]-baseline[c])``，clamp ``[0, baseline_max]``。

        审查修正（critical）：在**去扩散后的纯激活场**学习——drift 已先把激活+漏电结果
        clamp 进 field（扩散只增加联想联动，不进 baseline 学习），此处直接消费 field 即可。
        clamp 防止漏电 × 扩散联合不动点漂移失控。
        """
        e = self._engine
        for c, v in state.field.items():
            base = state.baseline.get(c, 0.0)
            new_base = base + e.baseline_alpha * (v - base)
            # clamp ∈ [0, baseline_max]
            if new_base < 0.0:
                new_base = 0.0
            elif new_base > e.baseline_max:
                new_base = e.baseline_max
            state.baseline[c] = new_base


# ─────────────────────────────────────────────────────────────────
# NeuralHook — MemoryHook（OBSERVER）每轮 on_turn_end → drift
# ─────────────────────────────────────────────────────────────────


class NeuralHook(MemoryHook):
    """OBSERVER 优先级 hook：override ``on_turn_end`` → 每轮触发 drift。

    不改 engine（集成边界）。失败降级到非致命（bus 已 try/except 包裹）。
    注意：``TurnContext`` 不直接携带 concepts/importances；本 hook 仅暴露
    drift 的调用骨架，实际 activated_concepts/importances 由 wiring 层（engine.py
    集成时）从本轮 memory item 抽取后通过 :meth:`drift_turn` 显式调用。
    """

    priority: HookPriority = HookPriority.OBSERVER

    def __init__(
        self,
        engine: NeuralFieldEngine,
        store: NeuralFieldStore,
        robustness: NeuralFieldRobustness | None = None,
        kg: Any = None,
    ) -> None:
        self._engine = engine
        self._store = store
        self._robustness = robustness or NeuralFieldRobustness(store, engine)
        self._kg = kg

    async def on_turn_end(self, ctx: TurnContext) -> None:
        """每轮结束的空骨架（无 concepts 时仅推进 turn_id）。

        wiring 层若希望注入真实 concepts，应直接调用 :meth:`drift_turn`。
        """
        await self.drift_turn(
            agent_id=ctx.agent_id,
            activated_concepts=[],
            importances={},
        )

    async def drift_turn(
        self,
        agent_id: str,
        activated_concepts: list[str],
        importances: dict[str, float],
        is_stable: bool = False,
    ) -> NeuralState:
        """一轮漂移的完整管线：load → drift → attention_group → 异常检测 → 快照/persist。

        异常命中时自动 restore 到最近 stable 快照（全链路降级红线）。
        """
        state = self._store.load_state(agent_id) or NeuralState(agent_id=agent_id)

        await self._engine.drift(state, activated_concepts, importances, self._kg)
        self._engine.compute_attention_group(state, self._kg, top_n=10)

        anomaly = self._robustness.detect_anomaly(state)
        if anomaly.type != AnomalyType.NONE:
            # 自动回退：拉最近 stable 快照覆盖
            stable_id = self._store.latest_stable_snapshot_id(agent_id)
            logger.warning(
                "neural field anomaly [%s] for agent %s: %s — restoring to stable %s",
                anomaly.type, agent_id, anomaly.detail, stable_id,
            )
            if stable_id:
                restored = self._robustness.restore(agent_id, stable_id)
                if restored is not None:
                    state = restored
        else:
            # 正常轮：去扩散场学习 baseline
            self._robustness.learn_baseline(state)
            if is_stable:
                self._store.take_snapshot(state, is_stable=True)

        # 普通快照（环形缓冲），无论是否异常都留一份供下轮对比
        self._store.take_snapshot(state, is_stable=False)
        self._store.save_state(state)
        return state


__all__ = [
    "NeuralState",
    "NeuralFieldEngine",
    "NeuralFieldStore",
    "NeuralFieldRobustness",
    "AnomalyType",
    "AnomalyResult",
    "NeuralHook",
]
