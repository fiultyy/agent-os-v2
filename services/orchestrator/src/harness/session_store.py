"""Orchestrator session registry: ext_id → native_sid mapping + metadata.

orche 自管持久层(方案 B+C):薄封装控制原生 harness turn,orche **不持有对话
内容**(真相在 claude transcript `~/.claude/projects/<slug>/<uuid>.jsonl` / claw
gateway 内部存储),只存"哪个 ext_id 对应哪个原生 session"+ 编排元数据。

- claude-code: native_sid = claude 完整 UUID(首 turn oneshot 后回填);ext_id =
  orche 12-hex 稳定句柄(前端/TUI/observe 引用)。续聊用 native_sid 走
  `claude --resume <native_sid>`。
- claw: native_sid = session_key = ext_id(create 时即有;gateway 隐式续聊)。

重启不丢:启动 load_all → 按 harness_type 分派重建 client(claude 无状态 /
claw 重连+subscribe),见 routes.restore_all_sessions()。

SQLite 风格对齐 services/observe/src/session_store.py(WAL +
check_same_thread=False)。依赖 start.py 的 pysqlite3 monkey-patch(同 observe)。
db 路径用 cwd 相对(与 pitfall/conversation registry 一致,避开 __file__ 层数
耦合 + start.py 的 os.chdir 旧路径风险)。
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional


def _default_db_path() -> str:
    """data/orch_sessions.db relative to cwd (engine.py mkdir data/). Env-overridable."""
    return os.getenv("ORCH_SESSIONS_DB", "data/orch_sessions.db")


SESSION_SCHEMA = """
CREATE TABLE IF NOT EXISTS orch_sessions (
    ext_id        TEXT PRIMARY KEY,      -- orche 外部句柄(前端/TUI/observe 引用)
    harness_type  TEXT NOT NULL,         -- 'claude-code' | 'claw'
    native_sid    TEXT,                  -- claude=完整 UUID(首turn回填) / claw=session_key(=ext_id)
    cwd           TEXT,                  -- claude working dir(claw 为 NULL)
    agent_id      TEXT,                  -- claw agent key(claude 为 NULL)
    created_at    TEXT NOT NULL,
    last_turn_at  TEXT
);

CREATE INDEX IF NOT EXISTS idx_orch_harness ON orch_sessions(harness_type, last_turn_at);
"""


class OrchSessionStore:
    """Persistent ext→native session mapping for the orchestrator.

    async-safe(single event loop):所有写(create/update_native_sid/touch/delete)
    在 orche 单 asyncio loop 串行执行。check_same_thread=False 放开线程归属检查
    仅为适配 loop,**非多线程并发写安全**;若将来 to_thread 并发写需换 aiosqlite /
    connection-per-thread + 显式串行锁(busy_timeout=5000 兜底单 loop 竞争)。
    ext_id 全局唯一 → PK;查询带 harness_type 过滤,claw key 与 claude ext 永不语义碰撞。
    """

    def __init__(self, db_path: Optional[str] = None) -> None:
        self._db_path: str = db_path or _default_db_path()
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection = sqlite3.connect(
            self._db_path, check_same_thread=False
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.executescript(SESSION_SCHEMA)
        self._conn.commit()

    # ── CRUD ──────────────────────────────────────────────────────────

    def create(
        self,
        ext_id: str,
        harness_type: str,
        native_sid: Optional[str] = None,
        cwd: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> None:
        """Register a session. INSERT OR IGNORE — re-create on restart is a no-op."""
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            """INSERT OR IGNORE INTO orch_sessions
               (ext_id, harness_type, native_sid, cwd, agent_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (ext_id, harness_type, native_sid, cwd, agent_id, now),
        )
        self._conn.commit()

    def get(self, harness_type: str, ext_id: str) -> Optional[Dict]:
        row = self._conn.execute(
            "SELECT * FROM orch_sessions WHERE harness_type=? AND ext_id=?",
            (harness_type, ext_id),
        ).fetchone()
        return dict(row) if row else None

    def list_all(self, harness_type: Optional[str] = None) -> List[Dict]:
        """List sessions, optionally filtered by harness_type.

        Ordered: most-recently-turned first, never-turned (NULL last_turn_at)
        after, tie-break by creation recency.
        """
        order = "(last_turn_at IS NULL), last_turn_at DESC, created_at DESC"
        if harness_type:
            rows = self._conn.execute(
                f"SELECT * FROM orch_sessions WHERE harness_type=? ORDER BY {order}",
                (harness_type,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                f"SELECT * FROM orch_sessions ORDER BY {order}"
            ).fetchall()
        return [dict(r) for r in rows]

    def update_native_sid(self, ext_id: str, native_sid: str) -> bool:
        """Backfill the native harness session id (claude-code first turn).

        Only writes if native_sid is currently NULL or differs — idempotent under
        repeated turn callbacks. Returns True if a row changed.
        """
        cur = self._conn.execute(
            """UPDATE orch_sessions SET native_sid=?
               WHERE ext_id=? AND (native_sid IS NULL OR native_sid<>?)""",
            (native_sid, ext_id, native_sid),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def touch(self, ext_id: str) -> None:
        """Bump last_turn_at after a turn."""
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "UPDATE orch_sessions SET last_turn_at=? WHERE ext_id=?", (now, ext_id)
        )
        self._conn.commit()

    def delete(self, harness_type: str, ext_id: str) -> bool:
        cur = self._conn.execute(
            "DELETE FROM orch_sessions WHERE harness_type=? AND ext_id=?",
            (harness_type, ext_id),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "OrchSessionStore":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
