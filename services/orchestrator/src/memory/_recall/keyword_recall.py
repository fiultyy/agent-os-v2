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
        results = [
            item for item in all_items
            if any(kw in item.content.lower() for kw in keywords)
        ]

        # P0-3 近因兜底:query 非空但无字面命中 → 返回该作用域最近 top_k 条
        # (all_items 已是 created_at DESC),消灭「问"之前聊过X"措辞不命中关键词
        # → 召回 0 → 失忆」。纯加法,不动 KEYWORD 匹配算法(_recall/ 红线)。
        # 对中文尤其关键:中文无空格,query.lower().split() 出单 token,整串子串
        # 匹配泛化为零,兜底是中文召回可用性的最后一道保险。
        if not results:
            return all_items[:top_k]

        # 召回质量兜底(importance 卫生闭环):匹配项内 low_info_reply 垫底 +
        # 同级 importance 降序。ingest 侧 importance 卫生已把失智/无信息回复
        # cap 0.3 + 标 metadata.low_info_reply=True(见 ingestor_agent.
        # _apply_importance_hygiene),召回层必须消费该标记 —— 否则失智回复恰好
        # 字面含 query 词(如「没有找到 Logseq 命令行工具的记录」)按近因/字面
        # 霸占第一位,盖住真维护记忆(实测 claw-03:imp0.3 失智排第一,imp0.56
        # 真维护排第二)。纯加法:KEYWORD 匹配判定(any kw in content)不变、P0-3
        # 近因兜底不变,仅收集全部命中项后重排取 top_k(原 len>=top_k 提前 break
        # 会漏掉后置高 importance 项,排序需看全量命中)。
        results.sort(
            key=lambda it: (
                bool((it.metadata or {}).get("low_info_reply")),  # False=非失智排前
                -float(it.importance or 0.0),                     # 同级 importance 降序
            )
        )
        return results[:top_k]
