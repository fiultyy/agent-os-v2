"""
RAGEngine - 语义检索引擎

实现 D-25 中的 Sideline Memory 层（RAG 部分）：
- 向量检索
- KG 实体关系检索
- 混合召回
"""

import logging
from typing import Dict, Any, List, Optional
from pathlib import Path

logger = logging.getLogger(__name__)


class RAGEngine:
    """
    RAG 检索引擎

    设计：
    - 使用 memory/ 目录的 FAISS 索引
    - KG 使用 SQLite 图数据库
    - 支持向量 + KG 混合召回
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.faiss_index = None
        self.kg_db = None
        self._embedding_provider = None  # lazy init in _embed_query
        self._init_faiss()
        self._init_kg()

    def _init_faiss(self):
        """初始化 FAISS 索引"""
        try:
            import faiss
            import sqlite3

            data_path = Path(self.config.get("data_path", "~/projects/agent-os/data"))
            index_file = data_path / "memory.faiss"
            meta_db_file = data_path / "memory.meta.db"

            if index_file.exists() and meta_db_file.exists():
                self.faiss_index = faiss.read_index(str(index_file))
                self.kg_db = sqlite3.connect(str(meta_db_file), check_same_thread=False)
        except Exception as e:
            logger.warning(f"Failed to initialize FAISS index: {e}")
            self.faiss_index = None

    def _init_kg(self):
        """初始化 KG 数据库"""
        if self.kg_db is None:
            try:
                import sqlite3
                data_path = Path(self.config.get("data_path", "~/projects/agent-os/data"))
                kg_db_file = data_path / "kg.db"
                if kg_db_file.exists():
                    self.kg_db = sqlite3.connect(str(kg_db_file), check_same_thread=False)
            except Exception as e:
                logger.warning(f"Failed to initialize KG database: {e}")
                self.kg_db = None

    def search(
        self,
        query: str,
        context: str = "",
        limit: int = 5
    ) -> List[Dict[str, Any]]:
        """
        语义搜索

        Args:
            query: 搜索查询
            context: 上下文（用于重排）
            limit: 返回数量

        Returns:
            [{"memory_id": str, "content": str, "score": float, "type": str}]
        """
        results = []

        # 1. 向量检索
        if self.faiss_index:
            vector_results = self._search_vector(query, limit)
            results.extend(vector_results)

        # 2. KG 检索
        if self.kg_db:
            kg_results = self._search_kg(query, limit)
            results.extend(kg_results)

        # 3. 去重和排序
        results = self._dedup(results)
        results = sorted(results, key=lambda x: x.get("score", 0), reverse=True)[:limit]

        return results

    def _search_vector(self, query: str, limit: int) -> List[Dict[str, Any]]:
        """向量检索"""
        if self.faiss_index is None:
            return []

        try:
            query_vector = self._embed_query(query)

            # FAISS 搜索
            D, I = self.faiss_index.search(query_vector, limit)

            # 从 meta.db 获取结果
            results = []
            for i, idx in enumerate(I[0]):
                if idx < 0:
                    continue
                meta = self._get_meta_by_idx(idx)
                if meta:
                    results.append({
                        "memory_id": meta.get("memory_id", f"vec_{idx}"),
                        "content": meta.get("content", ""),
                        "score": float(D[0][i]),
                        "type": "vector"
                    })
            return results
        except Exception as e:
            logger.warning(f"Vector search failed: {e}")
            return []

    def _embed_query(self, query: str):
        """生成查询文本的 embedding 向量。

        使用项目已有的 SentenceTransformerProvider（all-MiniLM-L6-v2，384 维）
        作为默认 embedding 后端。如果 embedding 服务不可用，退回到
        随机向量（仅用于测试/演示，会打印警告）。
        """
        import numpy as np

        # 尝试使用真实 embedding 服务
        try:
            from src.memory.embedding import SentenceTransformerProvider

            if not hasattr(self, '_embedding_provider') or self._embedding_provider is None:
                self._embedding_provider = SentenceTransformerProvider()

            vec = self._embedding_provider.embed(query)  # (dim,)
            # FAISS search 期望 (1, dim) 形状
            return vec.reshape(1, -1).astype('float32')
        except Exception as e:
            logger.warning(
                f"Embedding service unavailable, falling back to random vector: {e}"
            )
            # Fallback：仅当 embedding 服务不可用时使用随机向量
            dim = self.faiss_index.d if self.faiss_index else 384
            return np.random.rand(1, dim).astype('float32')

    def _get_meta_by_idx(self, idx: int) -> Optional[Dict]:
        """根据索引获取 metadata"""
        if self.kg_db is None:
            return None
        try:
            cursor = self.kg_db.execute(
                "SELECT memory_id, content FROM memories WHERE id = ?",
                (idx,)
            )
            row = cursor.fetchone()
            if row:
                return {"memory_id": row[0], "content": row[1]}
        except Exception:
            pass
        return None

    def _search_kg(self, query: str, limit: int) -> List[Dict[str, Any]]:
        """KG 实体检索"""
        if not self.kg_db:
            return []

        try:
            cursor = self.kg_db.execute(
                """
                SELECT e.entity_id, e.name, e.description
                FROM entities e
                WHERE e.name LIKE ? OR e.description LIKE ?
                LIMIT ?
                """,
                (f"%{query}%", f"%{query}%", limit)
            )

            results = []
            for row in cursor.fetchall():
                results.append({
                    "memory_id": row[0],
                    "content": row[2] or row[1],
                    "entity_name": row[1],
                    "score": 0.8,
                    "type": "kg"
                })

            return results
        except Exception:
            return []

    def _dedup(self, results: List[Dict]) -> List[Dict]:
        """去重"""
        seen = set()
        deduped = []
        for r in results:
            key = r.get("memory_id", "")
            if key not in seen:
                seen.add(key)
                deduped.append(r)
        return deduped

    def add_entity(self, memory_item: Dict[str, Any]) -> bool:
        """添加 KG 实体"""
        if not self.kg_db:
            return False

        try:
            self.kg_db.execute(
                """
                INSERT OR IGNORE INTO entities (entity_id, name, description, type)
                VALUES (?, ?, ?, ?)
                """,
                (
                    memory_item.get("memory_id", ""),
                    memory_item.get("title", ""),
                    memory_item.get("content", ""),
                    memory_item.get("memory_type", "general")
                )
            )
            self.kg_db.commit()
            return True
        except Exception:
            return False

    def add_vector(self, memory_item: Dict[str, Any]) -> bool:
        """添加向量索引"""
        # TODO: 实现向量生成和索引更新
        # 需要 embedding 模型
        logger.warning("add_vector() not fully implemented - vector index not updated")
        return False  # 返回 False 表示未真正实现

    def close(self):
        """关闭连接"""
        if self.kg_db:
            self.kg_db.close()
            self.kg_db = None