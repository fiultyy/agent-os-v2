# services/orchestrator/src/conversation/registry.py
"""Conversation history registry — SQLite-backed 对话历史持久化。

每次 ``/v1/execute`` 完成后,chat.py 调 :meth:`record_turn` 把这一轮
(user_input, assistant_response) 落库。前端经 gateway 调
``GET /v1/conversations`` 拉列表、``GET /v1/conversations/{id}/messages``
拉历史消息,实现"对话历史可回看 + 多对话切换"。

参照 :class:`src.pitfail.registry.PitfailRegistry` 模式:构造即 ``_init_db``
建表,每次操作新连接(``with sqlite3.connect``),失败由 engine.py try/except
降级为 None,call-site 以 ``is not None`` guard。
"""

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


class ConversationRegistry:
    """对话历史 CRUD。两张表:``conversations``(会话元数据)+ ``conversation_messages``(消息)。"""

    def __init__(self, db_path: str = "data/conversations.db") -> None:
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY,
                    agent_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS conversation_messages (
                    id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_conv_msg ON conversation_messages(conversation_id)"
            )

    def record_turn(
        self,
        session_id: str,
        agent_id: str,
        user_input: str,
        assistant_response: str,
    ) -> None:
        """记录一轮对话:upsert conversation(首次建,title=user_input[:30])+ 插入 user/assistant 两条消息。"""
        if not session_id or not agent_id:
            return
        title = (user_input or "").strip()[:30] or "(新对话)"
        now = self._now()
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                "SELECT id FROM conversations WHERE id = ?", (session_id,)
            )
            if cur.fetchone() is None:
                conn.execute(
                    "INSERT INTO conversations (id, agent_id, title, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                    (session_id, agent_id, title, now, now),
                )
            else:
                conn.execute(
                    "UPDATE conversations SET updated_at = ? WHERE id = ?",
                    (now, session_id),
                )
            conn.execute(
                "INSERT INTO conversation_messages (id, conversation_id, role, content, created_at) VALUES (?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), session_id, "user", user_input or "", now),
            )
            if assistant_response:
                conn.execute(
                    "INSERT INTO conversation_messages (id, conversation_id, role, content, created_at) VALUES (?, ?, ?, ?, ?)",
                    (str(uuid.uuid4()), session_id, "assistant", assistant_response, now),
                )

    def list_conversations(
        self, agent_id: Optional[str] = None, limit: int = 50
    ) -> list[dict]:
        """列表(newest-updated first),带 message_count。"""
        base_sql = (
            "SELECT c.id, c.agent_id, c.title, c.created_at, c.updated_at, "
            "(SELECT COUNT(*) FROM conversation_messages m WHERE m.conversation_id = c.id) AS msg_count "
            "FROM conversations c"
        )
        order = " ORDER BY c.updated_at DESC LIMIT ?"
        with sqlite3.connect(self.db_path) as conn:
            if agent_id:
                cur = conn.execute(
                    base_sql + " WHERE c.agent_id = ?" + order,
                    (agent_id, limit),
                )
            else:
                cur = conn.execute(base_sql + order, (limit,))
            return [
                {
                    "id": r[0],
                    "agent_id": r[1],
                    "title": r[2],
                    "created_at": r[3],
                    "updated_at": r[4],
                    "message_count": r[5],
                }
                for r in cur.fetchall()
            ]

    def list_messages(self, conversation_id: str, limit: int = 200) -> list[dict]:
        """某对话的消息(oldest first)。"""
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                "SELECT id, conversation_id, role, content, created_at "
                "FROM conversation_messages WHERE conversation_id = ? "
                "ORDER BY created_at ASC LIMIT ?",
                (conversation_id, limit),
            )
            return [
                {
                    "id": r[0],
                    "conversation_id": r[1],
                    "role": r[2],
                    "content": r[3],
                    "created_at": r[4],
                }
                for r in cur.fetchall()
            ]

    def delete_conversation(self, conversation_id: str) -> bool:
        """删除对话 + 级联删消息(显式删,不依赖 sqlite FK pragma)。"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "DELETE FROM conversation_messages WHERE conversation_id = ?",
                (conversation_id,),
            )
            cur = conn.execute(
                "DELETE FROM conversations WHERE id = ?", (conversation_id,)
            )
            return cur.rowcount > 0
