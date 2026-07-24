"""Pytest conftest — 统一 pysqlite3 注入(绕过 miniconda3 坏 sqlite3)+ _state 隔离。

miniconda3 的 ``_sqlite3.so`` 损坏(``undefined symbol: sqlite3_deserialize``),
任何 ``import sqlite3``(含 ``src.memory`` 链式 import)都会崩。本 conftest 在
collection 前把 ``sqlite3`` 替换为 ``pysqlite3``(来自 .venv),所有测试受益,
**无需各自顶部 patch**(消除 12 个测试重复 patch + 补 2 个遗漏)。

注:仅本地测试环境(miniconda3 python + .venv pysqlite3)需要。生产/CI 若
sqlite3 健康则 ``try/except`` 跳过,用 stdlib,不干预。

ADR-C1 autouse fixture ``_reset_state``:每个测试前调 ``_state.reset()`` 清空装配
单例 + 运行时累加器。根治 ``import engine`` 跨测试污染(前测试装配真 agents.yaml
→ ``_state.agent_registry`` 含 help/queen → 后测试 ``_build_native_session`` 拿
污染 registry;另 ``db_watcher``/``memory_observe_emitter`` 后台任务跨测试残留)。
"""

import sys

_VENV_SP = "/home/yy/projects/agent-os-v2/services/orchestrator/.venv/lib/python3.12/site-packages"
if _VENV_SP not in sys.path:
    sys.path.insert(0, _VENV_SP)

try:
    import pysqlite3  # type: ignore[import-untyped]

    sys.modules["sqlite3"] = pysqlite3
    try:
        sys.modules["sqlite3.dbapi2"] = pysqlite3.dbapi2
    except Exception:
        pass
except ImportError:
    # 无 pysqlite3(如 CI 健康 sqlite3)→ 用 stdlib,不干预
    pass


import pytest


@pytest.fixture(autouse=True)
def _reset_state():
    """ADR-C1: reset ``_state`` before every test for isolation.

    Clears assembled singletons (agent_registry, memory_service, llm_client, …) +
    runtime accumulators (agents, execution_log, runtime_observations,
    degraded_stats) back to module-load defaults. This runs AFTER any previous
    test's monkeypatch teardown, so a leaked singleton from an earlier test (or
    from ``import engine`` pulling in the old module-level assembly) cannot
    pollute the current test.

    Tests that wire ``_state.xxx`` themselves (with monkeypatch, or manual
    save/restore) do so AFTER this fixture runs, so their setup is unaffected.
    Tests that need a fully-bootstrapped engine call ``engine.bootstrap(<config>)``
    explicitly.
    """
    from src.services import _state

    _state.reset()
    yield
