"""
HermesWikiBaseline - Baseline 明文 wiki 搜索

实现 D-25 中的 Baseline 层：
- 轻量级明文搜索
- 不依赖 embedding 模型
- 直接读取 wiki 文件内容
"""

import re
from pathlib import Path
from typing import Dict, Any, List, Optional


class HermesWikiBaseline:
    """
    Baseline 明文搜索
    
    设计：
    - 搜索 ~/文档/kb/ 下的 wiki 文件
    - 使用简单正则匹配
    - 返回原始文本片段
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.wiki_path = Path(self.config.get("wiki_path", "~/文档/kb/")).expanduser()
        self._cache: Dict[str, List[str]] = {}  # filename -> lines

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

            except Exception:
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

        except Exception:
            return False

    def _read_lines(self, file_path: Path) -> List[str]:
        """读取文件行（带缓存）"""
        key = str(file_path)
        if key not in self._cache:
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    self._cache[key] = f.readlines()
            except Exception:
                self._cache[key] = []
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