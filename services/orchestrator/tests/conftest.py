"""Pytest conftest — 统一 pysqlite3 注入(绕过 miniconda3 坏 sqlite3)。

miniconda3 的 ``_sqlite3.so`` 损坏(``undefined symbol: sqlite3_deserialize``),
任何 ``import sqlite3``(含 ``src.memory`` 链式 import)都会崩。本 conftest 在
collection 前把 ``sqlite3`` 替换为 ``pysqlite3``(来自 .venv),所有测试受益,
**无需各自顶部 patch**(消除 12 个测试重复 patch + 补 2 个遗漏)。

注:仅本地测试环境(miniconda3 python + .venv pysqlite3)需要。生产/CI 若
sqlite3 健康则 ``try/except`` 跳过,用 stdlib,不干预。
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
