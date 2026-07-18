"""OrchSessionStore 持久层单测(方案 B+C):ext→native 映射 CRUD + 回填幂等。

验证薄封装的持久层语义:orche 只存映射+元数据,重启不丢,首 turn 回填 native
幂等。db 用 tmp_path 隔离,不碰 data/orch_sessions.db。
"""

from __future__ import annotations

from src.harness.session_store import OrchSessionStore


def _store(tmp_path) -> OrchSessionStore:
    return OrchSessionStore(str(tmp_path / "orch.db"))


def test_create_and_get(tmp_path):
    s = _store(tmp_path)
    s.create("abc123", "claude-code", native_sid=None, cwd="/tmp/proj")
    row = s.get("claude-code", "abc123")
    assert row is not None
    assert row["ext_id"] == "abc123"
    assert row["harness_type"] == "claude-code"
    assert row["native_sid"] is None          # 首 turn 前为 NULL
    assert row["cwd"] == "/tmp/proj"
    assert row["last_turn_at"] is None


def test_create_idempotent_preserves_native_on_restart(tmp_path):
    """重启 re-create(create_session 再跑)绝不能把 native_sid 清回 NULL。"""
    s = _store(tmp_path)
    s.create("abc", "claude-code", native_sid=None)
    s.update_native_sid("abc", "native-uuid-111")
    # 模拟重启:create 又以 native_sid=None 调一次
    s.create("abc", "claude-code", native_sid=None)
    assert s.get("claude-code", "abc")["native_sid"] == "native-uuid-111"


def test_claw_native_equals_ext(tmp_path):
    s = _store(tmp_path)
    s.create("agent:main:main", "claw",
             native_sid="agent:main:main", agent_id="main")
    row = s.get("claw", "agent:main:main")
    assert row["native_sid"] == "agent:main:main"
    assert row["agent_id"] == "main"


def test_update_native_sid_idempotent(tmp_path):
    s = _store(tmp_path)
    s.create("abc", "claude-code")
    assert s.update_native_sid("abc", "uuid-1") is True
    assert s.update_native_sid("abc", "uuid-1") is False   # 相同 → no-op
    assert s.update_native_sid("abc", "uuid-2") is True    # 不同 → 更新


def test_touch_updates_last_turn(tmp_path):
    s = _store(tmp_path)
    s.create("abc", "claude-code")
    assert s.get("claude-code", "abc")["last_turn_at"] is None
    s.touch("abc")
    assert s.get("claude-code", "abc")["last_turn_at"] is not None


def test_list_all_order_turned_first_null_last(tmp_path):
    s = _store(tmp_path)
    s.create("a", "claude-code")
    s.create("b", "claude-code")
    s.create("c", "claude-code")
    s.touch("c")
    s.touch("a")                       # a 最近 turn
    ids = [r["ext_id"] for r in s.list_all("claude-code")]
    assert ids[0] == "a"               # 最近 turn 居首
    assert ids[-1] == "b"              # 从未 turn(NULL)居末


def test_list_all_filter_by_harness(tmp_path):
    s = _store(tmp_path)
    s.create("a", "claude-code")
    s.create("k1", "claw", native_sid="agent:main:main")
    cc = s.list_all("claude-code")
    assert len(cc) == 1 and cc[0]["ext_id"] == "a"
    claw = s.list_all("claw")
    assert len(claw) == 1 and claw[0]["ext_id"] == "k1"


def test_delete(tmp_path):
    s = _store(tmp_path)
    s.create("abc", "claude-code")
    assert s.delete("claude-code", "abc") is True
    assert s.get("claude-code", "abc") is None
    assert s.delete("claude-code", "abc") is False


# ── native message_history 持久化(defer5)──────────────────────────────

def test_save_load_messages_roundtrip(tmp_path):
    s = _store(tmp_path)
    s.create("s1", "agent-os-v2", native_sid="s1")
    assert s.load_messages("s1") is None          # 未写 → None
    s.save_messages("s1", '{"kind":"request"}')
    assert s.load_messages("s1") == '{"kind":"request"}'
    s.save_messages("s1", '{"kind":"response"}')  # 覆盖
    assert s.load_messages("s1") == '{"kind":"response"}'


def test_messages_column_migrated_from_legacy_db(tmp_path):
    """旧库(无 messages 列)构造时 ALTER 补列,save/load 正常。"""
    import sqlite3
    db = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(db))
    conn.execute("""CREATE TABLE orch_sessions (
        ext_id TEXT PRIMARY KEY, harness_type TEXT NOT NULL, native_sid TEXT,
        cwd TEXT, agent_id TEXT, created_at TEXT NOT NULL, last_turn_at TEXT)""")
    conn.commit(); conn.close()
    s = OrchSessionStore(str(db))                  # 构造触发 _ensure_column
    s.create("s1", "agent-os-v2", native_sid="s1")
    s.save_messages("s1", '{"k":1}')
    assert s.load_messages("s1") == '{"k":1}'
