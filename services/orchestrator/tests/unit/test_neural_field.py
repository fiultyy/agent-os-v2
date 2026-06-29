# tests/unit/test_neural_field.py
"""Unit tests for neural_field module — ⑤ NeuralState 状态场 + ⑥ 鲁棒回退。

Covers (a) drift 收敛不发散(归一化)；(b) detect_anomaly 爆炸/塌缩/骤变；
(c) take_snapshot/restore；(d) learn_baseline clamp；(e) lif_weight 排名分位。

Design refs:
  - docs/memory-kernel-design.md 第 4 章 (4.1-4.5)
  - docs/memory-kernel-impl-plan.md Part 2 ⑤⑥ critical 修正
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile


# Patch corrupted miniconda sqlite3 with pysqlite3 before any import that
# transitively touches sqlite3 (mirrors start.py / other test modules).
try:
    import pysqlite3  # type: ignore[import-untyped]
    sys.modules["sqlite3"] = pysqlite3
    sys.modules["sqlite3.dbapi2"] = pysqlite3.dbapi2
except ImportError:  # pragma: no cover
    pass

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from memory.neural_field import (  # noqa: E402
    AnomalyType,
    NeuralFieldEngine,
    NeuralFieldRobustness,
    NeuralFieldStore,
    NeuralHook,
    NeuralState,
)


def run_async(coro):
    """Run a coroutine without leaving the main thread without an event loop.

    ``asyncio.run()`` closes (and nulls) the loop on exit, which breaks the
    deprecated ``asyncio.get_event_loop().run_until_complete()`` pattern still
    used by sibling test modules (e.g. test_butterfly_wing.py). We reuse a
    persistent module-level loop and keep it set so legacy callers still find
    one.
    """
    global _LOOP
    if _LOOP is None or _LOOP.is_closed():
        _LOOP = asyncio.new_event_loop()
        asyncio.set_event_loop(_LOOP)
    return _LOOP.run_until_complete(coro)


_LOOP = None


# ─────────────────────────────────────────────────────────────────
# Fake KG for drift/diffusion tests (duck-typed _KGLike)
# ─────────────────────────────────────────────────────────────────


class FakeKG:
    """最小 KG 鸭子：name→id、id→{name,source_memory_ids}、id→relations。

    relations: list of {"source_id","target_id","confidence"}。
    """

    def __init__(self, entities: dict[str, dict], relations: list[dict]) -> None:
        # entities: name -> {"id","name","source_memory_ids"}
        self._by_name = entities
        self._by_id = {e["id"]: e for e in entities.values()}
        self._relations = relations

    def _resolve_entity_id(self, name_or_id: str):
        if name_or_id in self._by_id:
            return name_or_id
        e = self._by_name.get(name_or_id)
        return e["id"] if e else None

    def get_entity(self, entity_id: str):
        return self._by_id.get(entity_id)

    def get_entity_relations(self, entity_id: str):
        out = []
        for r in self._relations:
            if r["source_id"] == entity_id or r["target_id"] == entity_id:
                out.append(r)
        return out


def _make_kg() -> FakeKG:
    entities = {
        "python": {"id": "e1", "name": "python", "source_memory_ids": ["m1", "m2"]},
        "asyncio": {"id": "e2", "name": "asyncio", "source_memory_ids": ["m3"]},
        "concurrency": {"id": "e3", "name": "concurrency", "source_memory_ids": ["m4"]},
        "rust": {"id": "e4", "name": "rust", "source_memory_ids": ["m5"]},
    }
    relations = [
        {"source_id": "e1", "target_id": "e2", "confidence": 1.0},
        {"source_id": "e1", "target_id": "e3", "confidence": 1.0},
        {"source_id": "e4", "target_id": "e3", "confidence": 1.0},
    ]
    return FakeKG(entities, relations)


# ─────────────────────────────────────────────────────────────────
# (a) drift 收敛不发散（归一化 / 谱半径约束）
# ─────────────────────────────────────────────────────────────────


class TestDriftConvergence:
    def test_field_clamped_to_unit_range(self):
        """激活量极大也必须 clamp 到 [0,1]，绝不发散。"""
        eng = NeuralFieldEngine()
        state = NeuralState(agent_id="a1")


        run_async(eng.drift(state, ["x"], {"x": 5.0}, kg=None))
        assert state.field["x"] <= 1.0
        assert state.field["x"] >= 0.0

    def test_drift_decays_toward_baseline_without_activation(self):
        """无激活时，field 向 baseline 漏电收敛（homeostasis，不归零）。"""
        eng = NeuralFieldEngine(decay=0.5)
        state = NeuralState(
            agent_id="a1",
            field={"x": 1.0},
            baseline={"x": 0.2},
        )


        for _ in range(10):
            run_async(eng.drift(state, [], {}, kg=None))
        # 应收敛到 baseline 0.2 附近（漏电不动点 = baseline）
        assert abs(state.field["x"] - 0.2) < 0.05

    def test_diffusion_normalized_does_not_explode(self):
        """扩散归一化：单概念高电位扩散到邻居后，邻居电位不会超过源电位。"""
        kg = _make_kg()
        eng = NeuralFieldEngine(decay=0.0, spread_rate=1.0)  # 关漏电，纯看扩散上界
        state = NeuralState(agent_id="a1", field={"python": 1.0})


        run_async(eng.drift(state, [], {}, kg=kg))
        # 邻居 asyncio/concurrency 收到注入，但因归一化 (w/out_degree=0.5)
        # 单轮注入 = 1.0 * 0.5 * 1.0 = 0.5，不超过源 1.0
        assert state.field.get("asyncio", 0.0) <= 1.0
        assert state.field.get("concurrency", 0.0) <= 1.0
        assert state.field.get("asyncio", 0.0) <= 1.0 + 1e-9

    def test_repeated_drift_bounded_under_diffusion(self):
        """连续多轮扩散+激活，整个场始终在 [0,1]，不发散（谱半径约束验证）。"""
        kg = _make_kg()
        eng = NeuralFieldEngine(decay=0.1, spread_rate=0.9)
        state = NeuralState(agent_id="a1")


        for _ in range(50):
            run_async(eng.drift(state, ["python", "rust"], {"python": 0.9, "rust": 0.8}, kg=kg))
        for c, v in state.field.items():
            assert 0.0 <= v <= 1.0, f"concept {c} potential {v} out of [0,1]"

    def test_diffusion_only_top_m_concepts(self):
        """扩散只对 top-M 高电位概念做，低电位概念不触发 KG 查询。"""
        kg = _make_kg()
        eng = NeuralFieldEngine(spread_top_m=1, decay=0.0, spread_rate=1.0)
        state = NeuralState(
            agent_id="a1",
            field={"python": 0.9, "rust": 0.1},
        )


        run_async(eng.drift(state, [], {}, kg=kg))
        # top-1 = python(0.9) 扩散；rust(0.1) 不扩散，其邻居不受影响
        # python 邻居是 asyncio/concurrency；rust 邻居是 concurrency
        # 因只扩散 top-1，concurrency 只收到来自 python 的注入
        assert "asyncio" in state.field  # python 的独占邻居


# ─────────────────────────────────────────────────────────────────
# (b) detect_anomaly — 爆炸 / 塌缩 / 骤变
# ─────────────────────────────────────────────────────────────────


def _fresh_store() -> NeuralFieldStore:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    store = NeuralFieldStore(db_path=path)
    store._tmp_path = path  # type: ignore[attr-defined]
    return store


class TestDetectAnomaly:
    def test_explosion_detected_with_hysteresis(self):
        """电位爆炸：>k*baseline 连续 2 turn 才确认（滞回）。"""
        eng = NeuralFieldEngine(explosion_k=5.0, hysteresis_turns=2)
        store = _fresh_store()
        rob = NeuralFieldRobustness(store, eng)
        state = NeuralState(
            agent_id="a1",
            field={"x": 1.0},
            baseline={"x": 0.05},  # 1.0 > 5*0.05=0.25
        )
        # 第一 turn：滞回未达，应是 pending/NONE
        r1 = rob.detect_anomaly(state)
        assert r1.type == AnomalyType.NONE
        # 第二 turn：连续 2 次，确认爆炸
        r2 = rob.detect_anomaly(state)
        assert r2.type == AnomalyType.EXPLOSION
        store.close()

    def test_collapse_detected_with_hysteresis(self):
        """场塌缩：Σfield 相对 < collapse_floor 连续 2 turn。"""
        eng = NeuralFieldEngine(collapse_floor=0.1, hysteresis_turns=2)
        store = _fresh_store()
        rob = NeuralFieldRobustness(store, eng)
        state = NeuralState(
            agent_id="a1",
            field={"x": 0.001, "y": 0.001},  # mean ≈ 0.001 < 0.1
        )
        r1 = rob.detect_anomaly(state)
        assert r1.type == AnomalyType.NONE
        r2 = rob.detect_anomaly(state)
        assert r2.type == AnomalyType.COLLAPSE
        store.close()

    def test_attention_shift_detected(self):
        """注意力骤变：>80% 连续 2 turn。需要先存一个 stable 快照作基线。"""
        eng = NeuralFieldEngine(attention_shift_ratio=0.8, hysteresis_turns=2)
        store = _fresh_store()
        rob = NeuralFieldRobustness(store, eng)
        # stable 基线快照
        base = NeuralState(
            agent_id="a1",
            field={"a": 0.9},
            attention_group=["m1", "m2"],
        )
        store.take_snapshot(base, is_stable=True)
        # 当前 attention_group 完全不同 → symmetric_difference/union = 1.0 > 0.8
        # baseline 设得高，避免被爆炸检测抢先触发
        cur = NeuralState(
            agent_id="a1",
            field={"b": 0.9},
            baseline={"b": 0.9},
            attention_group=["m9", "m8"],
        )
        r1 = rob.detect_anomaly(cur)
        assert r1.type == AnomalyType.NONE
        r2 = rob.detect_anomaly(cur)
        assert r2.type == AnomalyType.ATTENTION_SHIFT
        store.close()

    def test_no_anomaly_on_healthy_state(self):
        """健康状态（field 接近 baseline、注意力未骤变）→ NONE 且清零滞回。"""
        eng = NeuralFieldEngine()
        store = _fresh_store()
        rob = NeuralFieldRobustness(store, eng)
        state = NeuralState(
            agent_id="a1",
            field={"x": 0.2},
            baseline={"x": 0.2},
        )
        for _ in range(3):
            r = rob.detect_anomaly(state)
            assert r.type == AnomalyType.NONE
        store.close()


# ─────────────────────────────────────────────────────────────────
# (c) take_snapshot / restore
# ─────────────────────────────────────────────────────────────────


class TestSnapshotRestore:
    def test_snapshot_roundtrip(self):
        store = _fresh_store()
        state = NeuralState(
            agent_id="a1",
            field={"x": 0.5},
            attention_group=["m1"],
            baseline={"x": 0.1},
            turn_id=3,
        )
        sid = store.take_snapshot(state, is_stable=True)
        loaded = store.load_snapshot(sid)
        assert loaded is not None
        assert loaded.field == {"x": 0.5}
        assert loaded.attention_group == ["m1"]
        assert loaded.turn_id == 3
        store.close()

    def test_restore_overwrites_field(self):
        store = _fresh_store()
        eng = NeuralFieldEngine()
        rob = NeuralFieldRobustness(store, eng)
        stable = NeuralState(
            agent_id="a1",
            field={"good": 0.4},
            baseline={"good": 0.1},
        )
        sid = store.take_snapshot(stable, is_stable=True)
        # 当前场已漂坏
        bad = NeuralState(agent_id="a1", field={"bad": 1.0})
        store.save_state(bad)
        restored = rob.restore("a1", sid)
        assert restored is not None
        assert restored.field == {"good": 0.4}
        store.close()

    def test_ring_buffer_prunes_old_non_stable(self):
        """环形缓冲：非 stable 快照只保留最近 K 个；stable 永留。"""
        store = _fresh_store()
        store.RING_BUFFER_K = 3
        stable_sid = None
        non_stable_ids = []
        for i in range(6):
            state = NeuralState(agent_id="a1", field={"x": i * 0.1}, turn_id=i)
            if i == 0:
                stable_sid = store.take_snapshot(state, is_stable=True)
            else:
                non_stable_ids.append(store.take_snapshot(state, is_stable=False))
        # stable 仍在
        assert store.load_snapshot(stable_sid) is not None
        # 非 stable 只剩最近 3 个（i=3,4,5）

        rows = store._conn.execute(
            "SELECT snapshot_id FROM neural_snapshot WHERE agent_id=? AND is_stable=0",
            ("a1",),
        ).fetchall()
        assert len(rows) == 3
        store.close()

    def test_latest_stable_snapshot_id(self):
        store = _fresh_store()
        s1 = NeuralState(agent_id="a1", field={"x": 0.1})
        sid1 = store.take_snapshot(s1, is_stable=True)
        s2 = NeuralState(agent_id="a1", field={"x": 0.9})
        sid2 = store.take_snapshot(s2, is_stable=True)
        assert store.latest_stable_snapshot_id("a1") == sid2
        store.close()


# ─────────────────────────────────────────────────────────────────
# (d) learn_baseline clamp
# ─────────────────────────────────────────────────────────────────


class TestLearnBaseline:
    def test_baseline_moves_toward_field_and_clamps(self):
        """baseline 缓慢向 field 学习，且 clamp ∈ [0, baseline_max]。"""
        eng = NeuralFieldEngine(baseline_alpha=0.5, baseline_max=0.8)
        store = _fresh_store()
        rob = NeuralFieldRobustness(store, eng)
        state = NeuralState(
            agent_id="a1",
            field={"x": 1.0},  # 高电位
            baseline={"x": 0.1},
        )
        rob.learn_baseline(state)
        # new = 0.1 + 0.5*(1.0-0.1) = 0.55，未超 0.8
        assert abs(state.baseline["x"] - 0.55) < 1e-6
        store.close()

    def test_baseline_clamped_to_max(self):
        """field 远高于 baseline_max 时，baseline clamp 到 baseline_max。"""
        eng = NeuralFieldEngine(baseline_alpha=1.0, baseline_max=0.5)
        store = _fresh_store()
        rob = NeuralFieldRobustness(store, eng)
        state = NeuralState(
            agent_id="a1",
            field={"x": 1.0},
            baseline={"x": 0.1},
        )
        rob.learn_baseline(state)
        assert state.baseline["x"] == 0.5  # clamp 到上限
        store.close()

    def test_baseline_never_negative(self):
        eng = NeuralFieldEngine(baseline_alpha=1.0)
        store = _fresh_store()
        rob = NeuralFieldRobustness(store, eng)
        state = NeuralState(
            agent_id="a1",
            field={"x": 0.0},  # 低电位把 baseline 往下拉
            baseline={"x": 0.3},
        )
        rob.learn_baseline(state)
        assert state.baseline["x"] >= 0.0
        store.close()


# ─────────────────────────────────────────────────────────────────
# (e) lif_weight 排名分位（rank-based）
# ─────────────────────────────────────────────────────────────────


class TestLifWeight:
    def test_rank_based_percentile(self):
        """lif_weight 用相对排名分位，非绝对电位值。"""
        eng = NeuralFieldEngine()
        # 场中 4 个概念，电位各异（绝对值都很低，但分位有意义）
        fld = {"a": 0.02, "b": 0.04, "c": 0.06, "d": 0.08}
        # item 涉及最高电位概念 d → 分位应接近 1.0
        w_high = eng.lif_weight(["d"], fld)
        assert w_high > 0.9
        # item 涉及最低电位概念 a → 分位应接近 0.0
        w_low = eng.lif_weight(["a"], fld)
        assert w_low < 0.1

    def test_lif_weight_in_unit_range(self):
        eng = NeuralFieldEngine()
        fld = {"a": 0.5, "b": 0.3, "c": 0.9}
        w = eng.lif_weight(["a", "b", "c"], fld)
        assert 0.0 <= w <= 1.0

    def test_lif_weight_unknown_concept_zero(self):
        eng = NeuralFieldEngine()
        fld = {"a": 0.5}
        # item 涉及的概念不在场中 → 该概念分位 0，均值含 0
        w = eng.lif_weight(["a", "unknown"], fld)
        assert 0.0 <= w <= 1.0
        # 纯未知概念 → 0
        assert eng.lif_weight(["zzz"], fld) == 0.0

    def test_lif_weight_empty(self):
        eng = NeuralFieldEngine()
        assert eng.lif_weight([], {"a": 0.5}) == 0.0
        assert eng.lif_weight(["a"], {}) == 0.0


# ─────────────────────────────────────────────────────────────────
# NeuralHook drift_turn 集成骨架
# ─────────────────────────────────────────────────────────────────


class TestNeuralHookDriftTurn:
    def test_drift_turn_persists_and_snapshots(self):
        eng = NeuralFieldEngine()
        store = _fresh_store()
        kg = _make_kg()
        hook = NeuralHook(eng, store, kg=kg)


        run_async(
            hook.drift_turn(
                agent_id="a1",
                activated_concepts=["python"],
                importances={"python": 0.8},
                is_stable=True,
            )
        )
        loaded = store.load_state("a1")
        assert loaded is not None
        assert loaded.field.get("python", 0.0) > 0.0
        # is_stable=True → 应有一个 stable 快照
        assert store.latest_stable_snapshot_id("a1") is not None
        store.close()

    def test_drift_turn_auto_restore_on_anomaly(self):
        """drift_turn 命中爆炸异常时自动 restore 到最近 stable 快照。"""
        eng = NeuralFieldEngine(explosion_k=2.0, hysteresis_turns=2)
        store = _fresh_store()
        hook = NeuralHook(eng, store, kg=None)


        # 先建一个 stable 快照（健康场）
        healthy = NeuralState(
            agent_id="a1", field={"x": 0.1}, baseline={"x": 0.1}
        )
        store.save_state(healthy)
        store.take_snapshot(healthy, is_stable=True)

        # 接下来两轮都注入爆炸级激活
        for _ in range(2):
            run_async(
                hook.drift_turn(
                    agent_id="a1",
                    activated_concepts=["x"],
                    importances={"x": 1.0},
                )
            )
        loaded = store.load_state("a1")
        # 异常命中后应 restore 到 stable（field 回到健康态，不再是 1.0）
        assert loaded is not None
        assert loaded.field.get("x", 0.0) < 1.0
        store.close()
