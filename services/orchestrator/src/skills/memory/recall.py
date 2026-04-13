"""
memory_recall - 记忆检索

提供 Agent OS 记忆系统的检索接口：
- keyword: 关键字搜索
- semantic: 语义向量搜索
- kg: 知识图谱搜索
"""

from typing import Dict, Any, Optional, List


def memory_recall(
    query: str,
    mode: str = "keyword",
    limit: int = 10,
    threshold: float = 0.7,
    filters: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    检索记忆
    
    Args:
        query: 检索查询 (必填)
        mode: 检索模式，"keyword"(关键字)/"semantic"(语义)/"kg"(知识图谱)
        limit: 最大返回数量，默认 10
        threshold: 相关度阈值，默认 0.7
        filters: 额外过滤条件 (可选)
            - session_id: 限定会话
            - agent_id: 限定 Agent
            - memory_type: 记忆类型 (working/session/episodic/semantic)
    
    Returns:
        {
            "success": bool,
            "query": str,
            "mode": str,
            "results": List[dict],
            "count": int,
            "error": str (if failed)
        }
    
    Example:
        >>> result = memory_recall("project architecture", mode="semantic", limit=5)
        >>> if result["success"]:
        ...     for r in result["results"]:
        ...         print(f"[{r['score']:.2f}] {r['content'][:100]}...")
        >>>
        >>> # 关键字搜索 + 过滤器
        >>> result = memory_recall("decision", mode="keyword", filters={"memory_type": "semantic"})
    """
    result = {
        "success": False,
        "query": query,
        "mode": mode,
        "results": [],
        "count": 0,
        "error": None,
    }
    
    if not query:
        result["error"] = "Query is required"
        return result
    
    valid_modes = ["keyword", "semantic", "kg", "hybrid"]
    if mode not in valid_modes:
        result["error"] = f"Invalid mode: {mode}. Must be one of {valid_modes}"
        return result
    
    try:
        # 接入实际的 MemoryService
        from src.memory.service import MemoryService

        service = MemoryService()
        raw_results = service.search(
            query=query,
            mode=mode,
            limit=limit,
            threshold=threshold,
            filters=filters,
        )

        # 兼容：如果 MemoryService.search 返回 list 直接使用；
        # 如果返回 dict 则取 results 字段
        if isinstance(raw_results, dict):
            raw_results = raw_results.get("results", [])

        result["results"] = raw_results[:limit]
        result["count"] = len(result["results"])
        result["success"] = True
        
    except Exception as e:
        result["error"] = f"Recall error: {str(e)}"
    
    return result