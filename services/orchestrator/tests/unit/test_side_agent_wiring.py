"""Side-agent 装配契约测:env gate ON → _state 字段 wired + bus 注册对应 Hook。

重接 59695b1 归档(2026-08):engine.py 恢复 4 side agent(ingestor/consolidator/
retriever/curator)+ task_consolidator + side_llm gate 装配。本测守护**装配契约**,
不测 agent 内部逻辑(那是 tests/test_*_agent.py 的职责)。

隔离:conftest autouse 每测 ``_state.reset()`` → 必须在**测试体内**调
``engine_mod.bootstrap()``(设 env 后)。bootstrap 只构造单例不 start 后台 task
(start 在 FastAPI lifespan),故重复调用幂等,无 task 叠加风险。

bus._hooks 内省(``event_bus.py:105``):``dict[EventType, list[MemoryHook]]``,
register append + 按 priority 排序。``type(h).__name__`` 取 Hook 类名。
"""

from __future__ import annotations

import pytest

import src.engine as engine_mod
from src.memory.event_bus import EventType


def _hook_names(bus, event: EventType) -> list[str]:
    """bus 上某 event 已注册的 Hook 类名。"""
    return [type(h).__name__ for h in bus._hooks.get(event, [])]


# ── 4 side agent:env gate ON → wired + Hook 注册 ─────────────────────

def test_ingestor_wired_when_enabled(monkeypatch):
    monkeypatch.setenv("MEMORY_INGESTOR_ENABLED", "1")
    engine_mod.bootstrap()
    from src.services import _state
    assert _state.ingestor is not None, "MEMORY_INGESTOR_ENABLED=1 未装配 ingestor"
    names = _hook_names(_state.memory_event_bus, EventType.INGEST)
    assert "IngestorHook" in names, f"IngestorHook 未注册到 INGEST;got {names}"


def test_consolidator_wired_when_enabled(monkeypatch):
    monkeypatch.setenv("MEMORY_CONSOLIDATOR_ENABLED", "1")
    engine_mod.bootstrap()
    from src.services import _state
    assert _state.consolidator is not None
    # ConsolidatorHook 注册到 CONSOLIDATE + SESSION_END 两个 event。
    for ev in (EventType.CONSOLIDATE, EventType.SESSION_END):
        names = _hook_names(_state.memory_event_bus, ev)
        assert "ConsolidatorHook" in names, f"ConsolidatorHook 未注册到 {ev};got {names}"


def test_retriever_wired_when_enabled(monkeypatch):
    monkeypatch.setenv("MEMORY_RETRIEVER_ENABLED", "1")
    engine_mod.bootstrap()
    from src.services import _state
    assert _state.retriever is not None
    names = _hook_names(_state.memory_event_bus, EventType.RECALL)
    assert "RetrieverHook" in names, f"RetrieverHook 未注册到 RECALL;got {names}"


def test_curator_wired_when_enabled(monkeypatch):
    monkeypatch.setenv("MEMORY_CURATOR_ENABLED", "1")
    engine_mod.bootstrap()
    from src.services import _state
    assert _state.curator is not None
    names = _hook_names(_state.memory_event_bus, EventType.CURATE)
    assert "CuratorHook" in names, f"CuratorHook 未注册到 CURATE;got {names}"


# ── task_consolidator:无 gate,bootstrap 即装(修 /memory/consolidate 坏路径) ──

def test_task_consolidator_always_wired(monkeypatch):
    engine_mod.bootstrap()
    from src.services import _state
    assert _state.task_consolidator is not None, "task_consolidator 无 gate,bootstrap 必装"


# ── 默认全关(opt-in,零回归 native 主路径) ────────────────────────────

def test_all_disabled_by_default(monkeypatch):
    engine_mod.bootstrap()
    from src.services import _state
    assert _state.ingestor is None
    assert _state.consolidator is None
    assert _state.retriever is None
    assert _state.curator is None
    assert _state.side_llm_client is None  # SIDE_LLM_ENABLED 默认关
    # task_consolidator 无 gate,默认仍装
    assert _state.task_consolidator is not None
    # 默认无 side-agent Hook 注册到 bus
    assert "IngestorHook" not in _hook_names(_state.memory_event_bus, EventType.INGEST)
    assert "RetrieverHook" not in _hook_names(_state.memory_event_bus, EventType.RECALL)


# ── SIDE_LLM gate:ON → side_llm_client 装配;OFF → fallback 主 client ──

def test_side_llm_wired_when_enabled(monkeypatch):
    monkeypatch.setenv("SIDE_LLM_ENABLED", "1")
    # anthropic 通道需 api key;用占位避免 LLMClient 构造告警(不实际调用)
    monkeypatch.setenv("SIDE_LLM_ANTHROPIC_API_KEY", "test-placeholder")
    engine_mod.bootstrap()
    from src.services import _state
    assert _state.side_llm_client is not None, "SIDE_LLM_ENABLED=1 未装配 side_llm_client"


# ── fire 返 emit 结果(修 _state.py:97 fire-and-forget 丢返回值) ─────────
# 2c560c5(axis2)fire -> None 丢 bus.emit 返回值 → /v1/memories RECALL 走
# RetrieverHook scored 路径不可达(永走 service.recall fallback)。改 fire 返
# emit 结果后,memory.py:77 INGEST(result)/ :168 RECALL(ranked)能拿 hook 输出。

def test_fire_returns_bus_emit_result():
    """fire 返 bus.emit 的 hook 结果(非 None)。"""
    import asyncio
    from src.services import _state

    class _FakeBus:
        async def emit(self, event, ctx):
            return {"ranked": ["mem1", "mem2"]}

    _state.memory_event_bus = _FakeBus()
    try:
        result = asyncio.run(_state.fire("RECALL", {}))
        assert result == {"ranked": ["mem1", "mem2"]}, \
            f"fire 应返 emit 结果(非 None);got {result!r}"
    finally:
        _state.memory_event_bus = None


def test_fire_returns_none_when_bus_none():
    """bus=None → fire 返 None(向后兼容,test env 无 bus)。"""
    import asyncio
    from src.services import _state
    _state.memory_event_bus = None
    assert asyncio.run(_state.fire("RECALL", {})) is None


def test_fire_returns_none_on_emit_error():
    """emit 抛异常 → fire swallow + 返 None(不 raise,best-effort 守护)。"""
    import asyncio
    from src.services import _state

    class _BrokenBus:
        async def emit(self, event, ctx):
            raise RuntimeError("boom")

    _state.memory_event_bus = _BrokenBus()
    try:
        assert asyncio.run(_state.fire("RECALL", {})) is None, "emit 异常应 swallow 返 None"
    finally:
        _state.memory_event_bus = None
