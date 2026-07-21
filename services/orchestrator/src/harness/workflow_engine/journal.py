"""workflow_engine.journal — W-P2-1 跨进程 resume SQLite 事件溯源。

复用(最大化,design §4 复用清单):
- ``OrchSessionStore`` SQLite 风格(``session_store.py:62-74``):WAL +
  ``busy_timeout=5000`` + ``synchronous=NORMAL`` + ``check_same_thread=False``。
- ``start.py`` / ``tests/conftest.py`` 的 ``pysqlite3`` monkey-patch — 本模块
  ``import sqlite3`` 见到的是已 patch 过的 stdlib(**不另起 patch**;R1/R2 grep 机械
  守恒对 ``import sqlite3`` 无要求,sqlite3 不是 memory / 线性图原语)。

两表(DDL design §6.1):
- ``workflow_run``:run 级元数据(spec_json / status / total_usage / started_at)。
- ``workflow_event``:append-only 事件流(``started`` / ``result``),UNIQUE INDEX
  ``(run_id, key, type)`` 在 DB 层兜底 cache 命中唯一性 — 防半完成 agent 重跑产生
  双份结果(design §6.3 幂等保证)。

事件溯源 replay(``resume``):完成 agent(``type='result'``)走 cache 不重跑,
半完成(只有 ``started``)/未启动 agent 重跑 — 经 ``engine.run`` 复用并发逻辑。

RunUsage 序列化:pydantic-ai 2.0 ``RunUsage`` 无 ``from_json`` / ``to_json`` API
(verified),本模块自管 JSON 序列化(仅 round-trip 必需字段;``total_tokens`` 是
计算属性不存)。
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Optional

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────
# DDL(design §6.1 逐字)
# ─────────────────────────────────────────────────────────────────────
JOURNAL_SCHEMA = """
CREATE TABLE IF NOT EXISTS workflow_run (
  run_id        TEXT PRIMARY KEY,
  session_id    TEXT NOT NULL,
  parent_run_id TEXT,
  spec_json     TEXT NOT NULL,
  status        TEXT NOT NULL DEFAULT 'running',
  total_usage   TEXT,
  started_at    TEXT NOT NULL,
  finished_at   TEXT,
  error         TEXT
);
CREATE INDEX IF NOT EXISTS idx_wf_run_session ON workflow_run(session_id, started_at);
CREATE INDEX IF NOT EXISTS idx_wf_run_parent  ON workflow_run(parent_run_id);

CREATE TABLE IF NOT EXISTS workflow_event (
  event_id   TEXT PRIMARY KEY,
  run_id     TEXT NOT NULL REFERENCES workflow_run(run_id),
  seq        INTEGER NOT NULL,
  agent_id   TEXT,
  node_label TEXT,
  type       TEXT NOT NULL,
  key        TEXT NOT NULL,
  payload    TEXT NOT NULL,
  timestamp  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_wf_event_run ON workflow_event(run_id, seq);
CREATE UNIQUE INDEX IF NOT EXISTS uq_wf_event_key ON workflow_event(run_id, key, type);
"""


# ─────────────────────────────────────────────────────────────────────
# WorkflowEvent dataclass(行级 model;list_events / append_event 用)
# ─────────────────────────────────────────────────────────────────────
@dataclass
class WorkflowEvent:
    """workflow_event 行映射。

    ``type='started'``:``_spawn_agent`` 起(对位 CC journal.jsonl type=started)。
    ``type='result'``:``_spawn_agent`` 止(成功或失败) — 命中即 cache hit,resume
    跳过该 agent 不重跑。``key='v2:'+sha256(prompt+opts)[:16]`` 是 cache key。
    """

    event_id: str
    run_id: str
    seq: int
    agent_id: Optional[str]
    node_label: Optional[str]
    type: str                           # 'started' | 'result'
    key: str                            # 'v2:'+sha256(prompt+opts)[:16]
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: str = ""


def _default_db_path() -> str:
    """data/orch_workflow.db(独立文件避 OrchSessionStore 锁竞争,RK9 兜底)。

    env-overridable;cwd 相对(对齐 OrchSessionStore 风格,start.py 的 os.chdir
    上下文内落盘到正确位置)。
    """
    import os
    return os.getenv("ORCH_WORKFLOW_DB", "data/orch_workflow.db")


def _now_ts() -> str:
    return datetime.now(timezone.utc).isoformat()


def _cache_key(prompt: str, opts: dict[str, Any]) -> str:
    """'v2:' + sha256(json.dumps({prompt, opts}, sort_keys=True))[:16](design §4 复用)。"""
    blob = json.dumps({"prompt": prompt, "opts": opts}, sort_keys=True)
    return "v2:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


# RunUsage round-trip(pydantic-ai 2.0 无 from_json/to_json;verified)
# ponytail:仅 round-trip resume 必需字段;total_tokens 是 computed property 不存。
_USAGE_FIELDS = (
    "input_tokens",
    "cache_write_tokens",
    "cache_read_tokens",
    "output_tokens",
    "input_audio_tokens",
    "cache_audio_read_tokens",
    "output_audio_tokens",
    "requests",
    "tool_calls",
)


def usage_to_json(usage: Any) -> str:
    """RunUsage → JSON 字符串(journal total_usage 列)。

    防御性 getattr(usage, x, 0):测试 / partial RunUsage 缺字段时不崩。
    """
    return json.dumps({f: getattr(usage, f, 0) or 0 for f in _USAGE_FIELDS})


def usage_from_json(data: Optional[str]) -> Any:
    """JSON 字符串 → RunUsage(resume 重建 ctx.total_usage)。

    data=None / 空 → 返 fresh RunUsage()(design §6.3 ``RunUsage.from_json ...
    if row['total_usage'] else RunUsage()`` 语义,但 ``from_json`` 不存在 —
    自管重建)。
    """
    from pydantic_ai.usage import RunUsage
    if not data:
        return RunUsage()
    try:
        d = json.loads(data)
    except (ValueError, TypeError):
        return RunUsage()
    return RunUsage(**{f: int(d.get(f, 0) or 0) for f in _USAGE_FIELDS})


def _usage_from_dict(d: Any) -> Any:
    """result 事件 payload.usage(dict)→ RunUsage(F3 resume cached usage 重建)。

    payload 经 ``_usage_dict`` 写入(engine.py:428),字段对齐 ``_USAGE_FIELDS``。
    d=None / 缺字段 / 非数值 → 该字段 0(防御性,与 ``usage_from_json`` 同语义)。
    """
    from pydantic_ai.usage import RunUsage
    if not isinstance(d, dict):
        return RunUsage()
    return RunUsage(**{f: int(d.get(f, 0) or 0) for f in _USAGE_FIELDS})


def _merge_cached_usage(events: List["WorkflowEvent"]) -> Any:
    """遍历 ``type='result'`` 事件累加其 ``payload.usage`` → RunUsage(F3)。

    中断 run 的 ``workflow_run.total_usage`` 列为 NULL(``mark_completed`` 未调),
    resume 仅靠该列重建 ``ctx.total_usage`` 会丢 cached agent 用量。本辅助从事件流
    重建 cached usage(success node 的 usage 字段;error node 无 usage 字段 → 0,
    对位 engine ``_spawn_agent`` error 分支不计 usage 的语义)。
    """
    from pydantic_ai.usage import RunUsage
    total = RunUsage()
    for ev in events:
        if ev.type != "result":
            continue
        payload = ev.payload or {}
        # 仅 success node 累计(error node 无 usage 字段;design verify 断言 total_usage
        # 仅 success node 累计)。status 缺省按 success 兼容旧事件流。
        if payload.get("status", "success") != "success":
            continue
        total = total + _usage_from_dict(payload.get("usage"))
    return total


class WorkflowJournal:
    """跨进程 resume 的 SQLite 事件流存储。

    连接策略照搬 ``OrchSessionStore``(design §4 / RK9):
    - WAL + ``synchronous=NORMAL`` + ``busy_timeout=5000`` 兜底单 loop 写竞争。
    - ``check_same_thread=False``:orche 单 asyncio loop 串行写,**非多线程并发写
      安全**(若 to_thread 升级,换 aiosqlite + per-thread connection)。
    - 复用 ``start.py`` 的 ``pysqlite3`` monkey-patch — 本模块 ``import sqlite3``
      拿到的是已 patch 的 stdlib。

    resume 语义(design §6.3):
    - row.status ∈ {'completed','aborted'} → ``{'status':'replay_skip', ...}``。
    - 完成事件(``type='result'``)的 agent → cache hit 跳过。
    - 半完成(``type='started'`` only)/未启动 agent → 重跑(经 ``engine.run``)。
    - UNIQUE INDEX ``(run_id, key, type)`` 在 DB 层兜底 cache 命中唯一性。
    """

    def __init__(self, db_path: Optional[str] = None) -> None:
        self._db_path: str = db_path or _default_db_path()
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection = sqlite3.connect(
            self._db_path, check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.executescript(JOURNAL_SCHEMA)
        self._conn.commit()

    # ── run-level ─────────────────────────────────────────────────────

    def start_run(
        self,
        run_id: str,
        spec_json: str,
        session_id: str,
        parent_run_id: Optional[str] = None,
    ) -> None:
        """INSERT OR IGNORE — 重复 start_run 同 run_id 幂等(restart-safe)。"""
        now = _now_ts()
        self._conn.execute(
            """INSERT OR IGNORE INTO workflow_run
               (run_id, session_id, parent_run_id, spec_json, status, started_at)
               VALUES (?, ?, ?, ?, 'running', ?)""",
            (run_id, session_id, parent_run_id, spec_json, now),
        )
        self._conn.commit()

    def fetch_run(self, run_id: str) -> Optional[dict]:
        row = self._conn.execute(
            "SELECT * FROM workflow_run WHERE run_id=?", (run_id,),
        ).fetchone()
        return dict(row) if row else None

    def mark_completed(
        self,
        run_id: str,
        status: str,
        total_usage: Any,
        error: Optional[str] = None,
    ) -> None:
        """run 终态写入(status='completed'/'failed'/'aborted';finished_at)。"""
        now = _now_ts()
        self._conn.execute(
            """UPDATE workflow_run
               SET status=?, total_usage=?, finished_at=?, error=?
               WHERE run_id=?""",
            (status, usage_to_json(total_usage), now, error, run_id),
        )
        self._conn.commit()

    # ── event stream(append-only)────────────────────────────────────

    def append_event(
        self,
        run_id: str,
        agent_id: Optional[str],
        node_label: Optional[str],
        event_type: str,
        key: str,
        payload: dict[str, Any],
    ) -> WorkflowEvent:
        """append 一行 workflow_event。

        - ``seq`` 由 ``SELECT MAX(seq)+1`` 计算(单 loop 串行写,无并发竞争)。
        - ``key`` 由调用者算(``_cache_key``),UNIQUE INDEX ``(run_id,key,type)``
          在 DB 层兜底 cache 命中唯一性。
        - 返回插入的 WorkflowEvent(便于测试断言)。

        重复 INSERT(同 run_id+key+type)抛 ``sqlite3.IntegrityError`` — 设计行为
        (cache hit 双写防护,resume 并发兜底)。
        """
        row = self._conn.execute(
            "SELECT COALESCE(MAX(seq), -1) + 1 AS next_seq FROM workflow_event WHERE run_id=?",
            (run_id,),
        ).fetchone()
        seq = int(row["next_seq"])
        event_id = str(uuid.uuid4())
        ts = _now_ts()
        self._conn.execute(
            """INSERT INTO workflow_event
               (event_id, run_id, seq, agent_id, node_label, type, key, payload, timestamp)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event_id, run_id, seq, agent_id, node_label,
                event_type, key, json.dumps(payload, default=str), ts,
            ),
        )
        self._conn.commit()
        return WorkflowEvent(
            event_id=event_id, run_id=run_id, seq=seq,
            agent_id=agent_id, node_label=node_label,
            type=event_type, key=key, payload=payload, timestamp=ts,
        )

    def list_events(self, run_id: str) -> List[WorkflowEvent]:
        """ORDER BY seq 重放序列。"""
        rows = self._conn.execute(
            """SELECT * FROM workflow_event WHERE run_id=? ORDER BY seq""",
            (run_id,),
        ).fetchall()
        events: list[WorkflowEvent] = []
        for r in rows:
            try:
                payload = json.loads(r["payload"])
            except (ValueError, TypeError):
                payload = {}
            events.append(WorkflowEvent(
                event_id=r["event_id"], run_id=r["run_id"], seq=r["seq"],
                agent_id=r["agent_id"], node_label=r["node_label"],
                type=r["type"], key=r["key"], payload=payload,
                timestamp=r["timestamp"],
            ))
        return events

    # ── resume(事件溯源 replay)──────────────────────────────────────

    async def resume(self, run_id: str, engine: Any) -> dict[str, Any]:
        """事件溯源 replay(design §6.3 伪码逐字实现)。

        - 已完成 / aborted run → ``replay_skip``。
        - 完成事件(``type='result'``)的 agent payload 进 ``cached_results``,
          其 label 不进 pending。
        - 半完成(``started`` only)/未启动 node → 进 pending,经 ``engine.run`` 重跑。
        - 终态 ``mark_completed`` 写回(ctx.total_usage 已含 cached + fresh)。
        """
        row = self.fetch_run(run_id)
        if row is None:
            raise ValueError(f"unknown run_id: {run_id}")
        if row["status"] in ("completed", "aborted"):
            return {
                "status": "replay_skip",
                "reason": f"run already {row['status']}",
            }

        events = self.list_events(run_id)
        spec_dict = json.loads(row["spec_json"])
        # 延迟 import 避循环(WorkflowNodesSpec 在 engine.py)。
        from .engine import WorkflowContext, WorkflowNodesSpec
        from pydantic_ai.usage import RunUsage
        spec = WorkflowNodesSpec.model_validate(spec_dict)

        result_payloads: dict[str, dict] = {}    # agent_id → cached payload
        for ev in events:
            if ev.type == "result":
                result_payloads[ev.agent_id or ""] = ev.payload

        # pending nodes:label 不在 cached_results 的 node(对位 design §6.3 伪码)。
        cached_labels = {p.get("label") for p in result_payloads.values()}
        pending_nodes = [n for n in spec.nodes if n.label not in cached_labels]

        # F3:中断 run 的 row.total_usage 列为 NULL(``mark_completed`` 未调),
        # 仅靠该列重建 ctx.total_usage 会丢 cached agent 用量。从事件流重建 cached
        # usage,与 row.total_usage(若非 NULL,正常完成 run 二次 resume 路径)取大
        # 不重复累加 — 二者数据源重叠(cached result 即 row.total_usage 的子集),
        # row 非 NULL 时优先 row(权威),否则从事件流重建。
        row_usage = usage_from_json(row["total_usage"])
        if any(getattr(row_usage, f, 0) for f in _USAGE_FIELDS):
            # row.total_usage 非 fresh(权威 — 正常完成 run 二次 resume 路径)。
            ctx_total = row_usage
        else:
            ctx_total = _merge_cached_usage(events)

        ctx = WorkflowContext(
            session_id=row["session_id"],
            agent_id_prefix=f"wf_{run_id[:8]}",
            run_id=run_id,
            total_usage=ctx_total,
            journal=self,
        )

        fresh_node_count = len(pending_nodes)
        if pending_nodes:
            fresh_spec = WorkflowNodesSpec(
                nodes=pending_nodes,
                fan_in=spec.fan_in,
                timeout_per_node_ms=spec.timeout_per_node_ms,
            )
            await engine.run(fresh_spec, ctx)
        # ctx.total_usage 经 engine.run 内 _spawn_agent fan-in commit 累加 fresh usage;
        # cached usage 已在 ctx 构造前从事件流重建(F3 _merge_cached_usage)。

        self.mark_completed(run_id, status="completed", total_usage=ctx.total_usage)
        return {
            "status": "resumed",
            "cached": len(result_payloads),
            "fresh": fresh_node_count,
            "total_usage": _usage_to_dict(ctx.total_usage),
        }

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "WorkflowJournal":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def _usage_to_dict(usage: Any) -> dict[str, int]:
    """Resume 返回 payload 的 usage 摘要(同 engine._usage_dict,延迟 import 避循环)。"""
    return {
        "requests": getattr(usage, "requests", 0) or 0,
        "input_tokens": getattr(usage, "input_tokens", 0) or 0,
        "output_tokens": getattr(usage, "output_tokens", 0) or 0,
        "total_tokens": getattr(usage, "total_tokens", 0) or 0,
    }
