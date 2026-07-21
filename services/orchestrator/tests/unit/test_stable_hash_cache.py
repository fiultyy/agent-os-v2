"""G1: stable prefix hash 缓存单测。

覆盖:
- 同 (layer, source, content) 二次 get_instructions 返同 str 且 _stable_cache 命中
- 不同 content → 不同 key
- LRU 上限 32 淘汰(造 33 项看第一项被淘汰)

ponytail: 直接断言 _stable_cache dict 状态,不引 mock。
"""

from __future__ import annotations

from src.harness.capabilities import EngineeringDisciplineCapability, LayerCapability
from src.harness.capabilities import _stable_cache as stable_cache_module
from src.harness.capabilities._stable_cache import _cached


def _reset_cache() -> None:
    stable_cache_module._stable_cache.clear()


def test_layer_capability_same_args_returns_same_string_and_hits_cache() -> None:
    _reset_cache()
    cap = LayerCapability(layer=2, source="rules", content="Be terse.")
    first = cap.get_instructions()
    second = cap.get_instructions()
    assert first == second == "=== rules (L2) ===\nBe terse."
    # 缓存里恰好 1 条(LayerCapability 的 key)
    assert len(stable_cache_module._stable_cache) == 1


def test_layer_capability_different_content_different_key() -> None:
    _reset_cache()
    a = LayerCapability(layer=0, source="soul", content="A")
    b = LayerCapability(layer=0, source="soul", content="B")
    assert a.get_instructions() == "=== soul (L0) ===\nA"
    assert b.get_instructions() == "=== soul (L0) ===\nB"
    # 不同 content → 两个不同 key
    assert len(stable_cache_module._stable_cache) == 2


def test_layer_capability_empty_content_also_cached() -> None:
    """空 content 也走同路径(ponytail: 不对空串短路,统一缓存)。"""
    _reset_cache()
    cap = LayerCapability(layer=0, source="x", content="")
    assert cap.get_instructions() == "=== x (L0) ===\n"
    assert len(stable_cache_module._stable_cache) == 1


def test_engineering_discipline_cached_across_calls() -> None:
    _reset_cache()
    cap = EngineeringDisciplineCapability()
    first = cap.get_instructions()
    second = cap.get_instructions()
    assert first == second
    assert "Engineering Discipline" in first
    assert len(stable_cache_module._stable_cache) == 1


def test_engineering_discipline_disabled_and_custom_text_cached_separately() -> None:
    _reset_cache()
    enabled_default = EngineeringDisciplineCapability()
    disabled = EngineeringDisciplineCapability(enabled=False)
    custom = EngineeringDisciplineCapability(discipline_text="CUSTOM-TEXT")
    assert enabled_default.get_instructions() != ""
    assert disabled.get_instructions() == ""
    assert custom.get_instructions() == "CUSTOM-TEXT"
    # 3 种不同 key_parts → 3 条缓存
    assert len(stable_cache_module._stable_cache) == 3


def test_lru_limit_32_evicts_oldest() -> None:
    """造 33 项,第一项应被淘汰(粗 LRU: next(iter) pop)。"""
    _reset_cache()
    first_key_parts = ("lru", "seed-0", "first")
    _cached(first_key_parts, lambda: "VALUE-0")
    assert len(stable_cache_module._stable_cache) == 1
    # 再造 32 项不同 key,总共 33 → 触发淘汰,seed-0 应被 pop
    for i in range(1, 33):
        _cached(("lru", f"seed-{i}", str(i)), lambda i=i: f"VALUE-{i}")
    assert len(stable_cache_module._stable_cache) == 32
    # 第一项已被淘汰 —— 重新调应触发 build(用 sentinel 计数确认 miss)
    build_calls = {"n": 0}

    def rebuild() -> str:
        build_calls["n"] += 1
        return "VALUE-0-REBUILT"

    result = _cached(first_key_parts, rebuild)
    assert result == "VALUE-0-REBUILT"
    assert build_calls["n"] == 1   # miss → rebuild 被调一次(若未淘汰则 0)


def test_cache_hit_does_not_call_build() -> None:
    """命中时 build 不被调(哨兵)。"""
    _reset_cache()
    build_calls = {"n": 0}

    def build() -> str:
        build_calls["n"] += 1
        return "X"

    _cached(("hit", "test"), build)
    _cached(("hit", "test"), build)
    _cached(("hit", "test"), build)
    assert build_calls["n"] == 1   # 三次调用只 build 一次
