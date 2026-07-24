#!/usr/bin/env python3
"""Startup wrapper: replace broken sqlite3 with pysqlite3 before any app imports.

The venv symlinks to miniconda3 python3.12 whose sqlite3 native module
is corrupted.  We must monkey-patch sqlite3 → pysqlite3 at the *very*
beginning of the process, before any transitive import touches sqlite3.

All application imports (engine, services, etc.) happen *after* the patch.

启动:python services/orchestrator/start.py
   (pysqlite3 patch 方案;LD_PRELOAD 方案见根 start.sh / Makefile dev-orch。两者择一。)
"""

import sys

# ── Phase 1: pysqlite3 patch (before ANY other import) ─────────────
import pysqlite3  # noqa: E402
sys.modules["sqlite3"] = pysqlite3
sys.modules["sqlite3.dbapi2"] = pysqlite3.dbapi2

# ── Phase 2: application bootstrap (safe to import now) ───────────
if __name__ == "__main__":
    import os
    import uvicorn

    # 相对 chdir(start.py 所在 services/orchestrator),避免写死旧 agent-os 绝对路径。
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, "src")

    # Import engine only AFTER pysqlite3 is patched — 保证每个模块(vector.py /
    # event_store.py 等)import sqlite3 时看到 patched 版本。
    from engine import app

    uvicorn.run(app, host="127.0.0.1", port=int(os.getenv("ORCHESTRATOR_PORT", "8001")), log_level="info")
