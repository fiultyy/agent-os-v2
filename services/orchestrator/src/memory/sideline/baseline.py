"""
HermesWikiBaseline - Baseline 明文 wiki 搜索

实现 D-25 中的 Baseline 层：
- 轻量级明文搜索
- 不依赖 embedding 模型
- 直接读取 wiki 文件内容
"""

import logging
import re
from collections import OrderedDict
from pathlib import Path
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)


class HermesWikiBaseline:
    """
    Baseline 明文搜索
    
    设计：
    - 搜索 ~/文档/kb/ 下的 wiki 文件
    - 使用简单正则匹配
    - 返回原始文本片段
    """

    # LRU 缓存配置
    _CACHE_MAXSIZE = 100

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.wiki_path = Path(self.config.get("wiki_path", "~/文档/kb/")).expanduser()
        self._cache: OrderedDict[str, List[str]] = OrderedDict()  # LRU cache: filename -> lines

    def search(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        """
        搜索 wiki 明文

        Args:
            query: 搜索词
            limit: 返回结果数量

        Returns:
            [{"file": str, "line": int, "text": str, "relevance": float}]
        """
        results = []
        query_lower = query.lower()

        # 扫描所有 md 文件
        if not self.wiki_path.exists():
            return results

        for md_file in self.wiki_path.glob("**/*.md"):
            try:
                # 读取文件（带缓存）
                lines = self._read_lines(md_file)

                # 简单行搜索
                for i, line in enumerate(lines):
                    if query_lower in line.lower():
                        # 计算相关性分数
                        relevance = self._calc_relevance(line, query)

                        results.append({
                            "file": str(md_file),
                            "line": i + 1,
                            "text": line.strip(),
                            "relevance": relevance,
                            "type": "wiki"
                        })

                        if len(results) >= limit:
                            return sorted(results, key=lambda x: x["relevance"], reverse=True)

            except Exception as e:
                logger.warning(f"Failed to process file {md_file}: {e}")
                continue

        return sorted(results, key=lambda x: x["relevance"], reverse=True)[:limit]

    def write(self, memory_item: Dict[str, Any]) -> bool:
        """
        写入 wiki 明文

        实现 Backward 流程第一步：
        1. 追加到 wiki 文件
        2. 不修改 file_graph（由调用方处理）
        """
        try:
            content = memory_item.get("content", "")
            if not content:
                return False

            # 简单实现：写入日期命名的文件
            from datetime import datetime
            date_str = datetime.now().strftime("%Y-%m-%d")
            wiki_file = self.wiki_path / f"memory_{date_str}.md"

            # 追加内容
            with open(wiki_file, "a", encoding="utf-8") as f:
                f.write(f"\n## {memory_item.get('title', 'Memory Entry')}\n")
                f.write(f"{content}\n")
                f.write(f"\n<!-- memory_id: {memory_item.get('memory_id', 'unknown')} -->\n")

            # 清空缓存
            if str(wiki_file) in self._cache:
                del self._cache[str(wiki_file)]

            return True

        except Exception as e:
            logger.error(f"Failed to write memory item: {e}")
            return False

    def batch_search(self, queries: List[str], limit_per_query: int = 5) -> Dict[str, List[Dict]]:
        """批量搜索多个查询"""
        results = {}
        for query in queries:
            results[query] = self.search(query, limit=limit_per_query)
        return results

    def get_stats(self) -> Dict[str, Any]:
        """获取 baseline 统计信息"""
        cache_size = len(self._cache)
        cached_files = list(self._cache.keys()) if hasattr(self, '_cache') else []
        return {
            "cache_size": cache_size,
            "cached_files": cached_files,
            "wiki_path": str(self.wiki_path),
            "wiki_path_exists": self.wiki_path.exists()
        }

    def _read_lines(self, file_path: Path) -> List[str]:
        """读取文件行（带 LRU 缓存）"""
        key = str(file_path)
        if key not in self._cache:
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    lines = f.readlines()
            except Exception as e:
                logger.warning(f"Failed to read file {file_path}: {e}")
                lines = []
            # 淘汰最旧条目
            if len(self._cache) >= self._CACHE_MAXSIZE:
                self._cache.popitem(last=False)
            self._cache[key] = lines
        else:
            # 命中时移到末尾（最近使用）
            self._cache.move_to_end(key)
        return self._cache[key]

    def _calc_relevance(self, line: str, query: str) -> float:
        """计算相关性分数"""
        line_lower = line.lower()
        query_lower = query.lower()

        score = 0.0

        # 精确匹配
        if query_lower in line_lower:
            score += 0.5

        # 词边界匹配
        words = query_lower.split()
        for word in words:
            if re.search(r'\b' + re.escape(word) + r'\b', line_lower):
                score += 0.2

        # 长度惩罚（太长的行相关性降低）
        if len(line) > 200:
            score *= 0.8

        return min(1.0, score)