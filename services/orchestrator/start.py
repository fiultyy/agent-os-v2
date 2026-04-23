#!/usr/bin/env python3
"""Startup wrapper: replace broken sqlite3 with pysqlite3 before any app imports."""

import sys
import importlib

# Replace sqlite3 with pysqlite3 BEFORE any other imports use sqlite3
import pysqlite3
sys.modules["sqlite3"] = pysqlite3
sys.modules["sqlite3.dbapi2"] = pysqlite3.dbapi2

# Now import and run uvicorn with the app
if __name__ == "__main__":
    import os
    os.chdir("/home/yy/projects/agent-os/services/orchestrator")
    sys.path.insert(0, "src")
    
    from engine import app
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=18792, log_level="info")