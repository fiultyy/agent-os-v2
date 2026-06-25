"""Keyword-based recall strategy."""

from src.memory.store import InMemoryStore
from src.memory.types import MemoryFilter, MemoryItem, MemoryType, MemoryScope

from .base import RecallStrategy


class KeywordRecall(RecallStrategy):
    """Case-insensitive keyword matching recall."""

    def __init__(self, store: InMemoryStore) -> None:
        self._store = store

    async def recall(
        self,
        query: str,
        agent_id: str,
        session_id: str,
        memory_type: MemoryType | None,
        scope: MemoryScope | None,
        top_k: int,
    ) -> list[MemoryItem]:
        f = MemoryFilter(
            agent_id=agent_id,
            session_id=session_id,
            memory_type=memory_type,
            scope=scope,
        )

        all_items = await self._store.search(f)

        if not query.strip():
            return all_items[:top_k]

        keywords = query.lower().split()
        results = []
        for item in all_items:
            content_lower = item.content.lower()
            if any(kw in content_lower for kw in keywords):
                results.append(item)
                if len(results) >= top_k:
                    break

        # P0-3 近因兜底:query 非空但无字面命中 → 返回该作用域最近 top_k 条
        # (all_items 已是 created_at DESC),消灭「问"之前聊过X"措辞不命中关键词
        # → 召回 0 → 失忆」。纯加法,不动 KEYWORD 匹配算法(_recall/ 红线)。
        # 对中文尤其关键:中文无空格,query.lower().split() 出单 token,整串子串
        # 匹配泛化为零,兜底是中文召回可用性的最后一道保险。
        if not results:
            return all_items[:top_k]

        return results
