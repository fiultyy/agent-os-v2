#!/usr/bin/env python3
"""Startup wrapper: replace broken sqlite3 with pysqlite3 before any app imports.

The venv symlinks to miniconda3 python3.12 whose sqlite3 native module
is corrupted.  We must monkey-patch sqlite3 → pysqlite3 at the *very*
beginning of the process, before any transitive import touches sqlite3.

All application imports (engine, services, etc.) happen *after* the patch.
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

    os.chdir("/home/yy/projects/agent-os/services/orchestrator")
    sys.path.insert(0, "src")

    # Import engine only AFTER pysqlite3 is patched.
    # This guarantees that every module (vector.py, event_store.py, etc.)
    # sees the patched sqlite3 when it does `import sqlite3`.
    from engine import app

    uvicorn.run(app, host="127.0.0.1", port=18792, log_level="info")

# ── Notes ───────────────────────────────────────────────────────
#
# resource-manager micro-service:
#   This orchestrator handles agent execution, memory, and canvas.
#   The resource-manager service (provider config, model routing) is
#   a separate micro-service that must be started independently.
#   See services/resource-manager/README.md for details.
#
#   To start:  cd services/resource-manager && python start.py