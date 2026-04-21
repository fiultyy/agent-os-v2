"""ReuseTracker — 跟踪工具调用，计算 KG entity 复用积分。

关注点：
- 仅跟踪 assistant 的 tool_calls（不处理 tool_results/返回内容）
- tool_call 的 name 映射到 KG entity
- 每次工具被调用，entity 的 reuse_score += 1
- 按时间衰减：30 天前的积分减半
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any

from src.memory.knowledge_graph import Entity, KnowledgeGraph

logger = logging.getLogger(__name__)


class ReuseTracker:
    """工具复用积分跟踪器。

    职责：
    1. 从 ActionUnit.tool_calls 提取工具名
    2. 查找/创建对应的 KG entity
    3. 增加 reuse_score
    4. 提供按 entity 或时间范围的复用统计
    """

    def __init__(self, knowledge_graph: KnowledgeGraph) -> None:
        self._kg = knowledge_graph

    def track_tool_call(self, tool_name: str, context: dict[str, Any]) -> dict[str, Any]:
        """跟踪一次工具调用，增加复用积分。

        Args:
            tool_name: 工具名称（如 "kg_memory_query"）。
            context: 上下文信息（session_id, agent_id, timestamp, action_unit_id）。

        Returns:
            ``{"entity_id": str, "old_score": float, "new_score": float}``
        """
        if not tool_name:
            return {"entity_id": "", "old_score": 0.0, "new_score": 0.0}

        # 1. 查找或创建 KG entity（entity_type="tool"）
        entity = self._kg.find_entity_by_name(tool_name)
        if not entity:
            entity_id = self._kg.add_entity(Entity(
                name=tool_name,
                entity_type="tool",
                properties={"reuse_history": []},
            ))
            entity = self._kg.get_entity(entity_id)
        else:
            entity_id = entity["id"]

        # 2. 读取当前 reuse_score
        props: dict[str, Any] = entity.get("properties", {})
        current_score = float(props.get("reuse_score", 0.0))

        # 3. +1 积分
        new_score = current_score + 1.0

        # 4. 更新 reuse_history
        history: list[dict[str, Any]] = props.get("reuse_history", [])
        history.append({
            "timestamp": context.get("timestamp", datetime.now(timezone.utc).isoformat()),
            "action_unit_id": context.get("action_unit_id", ""),
            "session_id": context.get("session_id", ""),
            "score_delta": 1.0,
        })
        # 保留最近 100 条
        history = history[-100:]

        # 5. 写回
        new_props = {**props, "reuse_score": new_score, "reuse_history": history}
        self._kg._conn.execute(
            "UPDATE entities SET properties = ?, updated_at = ? WHERE id = ?",
            (json.dumps(new_props, ensure_ascii=False),
             datetime.now(timezone.utc).isoformat(),
             entity_id),
        )
        self._kg._conn.commit()

        return {"entity_id": entity_id, "old_score": current_score, "new_score": new_score}

    def get_top_reused_entities(
        self,
        entity_type: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """获取复用积分最高的 entities。

        Args:
            entity_type: 可选的 entity 类型过滤。
            limit: 返回数量上限。

        Returns:
            按 reuse_score 降序排列的 entity 列表。
        """
        sql = """
            SELECT id, name, type, properties, created_at
            FROM entities
            WHERE properties LIKE '%reuse_score%'
        """
        params: list[Any] = []
        if entity_type:
            sql += " AND type = ?"
            params.append(entity_type)

        # SQLite 没有直接从 JSON 提取排序的能力，用 Python 排序
        rows = self._kg._conn.execute(sql, params).fetchall()
        results: list[dict[str, Any]] = []
        for row in rows:
            props = json.loads(row["properties"]) if row["properties"] else {}
            score = float(props.get("reuse_score", 0.0))
            results.append({
                "id": row["id"],
                "name": row["name"],
                "entity_type": row["type"] or "",
                "reuse_score": score,
            })
        results.sort(key=lambda x: x["reuse_score"], reverse=True)
        return results[:limit]

    def decay_scores(self, days: int = 30) -> dict[str, int | float]:
        """对历史积分进行时间衰减。

        对超过 *days* 天的 reuse_history 条目施加衰减（积分数值减半）。
        实际操作：重新计算 reuse_score = sum(decayed_deltas)。

        Args:
            days: 衰减阈值天数（默认 30）。

        Returns:
            ``{"entities_updated": int, "total_decay": float}``
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        cutoff_iso = cutoff.isoformat()

        rows = self._kg._conn.execute(
            "SELECT id, properties FROM entities WHERE properties LIKE '%reuse_history%'",
        ).fetchall()

        updated = 0
        total_decay = 0.0

        for row in rows:
            props = json.loads(row["properties"]) if row["properties"] else {}
            history: list[dict[str, Any]] = props.get("reuse_history", [])
            if not history:
                continue

            old_score = float(props.get("reuse_score", 0.0))
            new_score = 0.0
            for entry in history:
                ts = entry.get("timestamp", "")
                delta = float(entry.get("score_delta", 1.0))
                if ts < cutoff_iso:
                    delta *= 0.5  # 衰减：减半
                new_score += delta

            if new_score != old_score:
                decay_amount = old_score - new_score
                total_decay += decay_amount
                props["reuse_score"] = new_score
                self._kg._conn.execute(
                    "UPDATE entities SET properties = ? WHERE id = ?",
                    (json.dumps(props, ensure_ascii=False), row["id"]),
                )
                updated += 1

        self._kg._conn.commit()
        logger.info("Reuse score decay: %d entities updated, %.2f total decay", updated, total_decay)
        return {"entities_updated": updated, "total_decay": total_decay}
