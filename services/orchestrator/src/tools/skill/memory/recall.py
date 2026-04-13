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
        # 模拟检索结果
        # 实际实现需要集成 Agent OS 的 MemoryService
        mock_results = [
            {
                "id": "mem_001",
                "content": "Architecture decision: Use Layer pattern for tool abstraction. D-13.",
                "memory_type": "semantic",
                "score": 0.95,
                "timestamp": "2026-04-13T10:00:00Z",
                "source": "context_compiler",
            },
            {
                "id": "mem_002",
                "content": "Remember: Always use Store Protocol for store implementations. D-12.",
                "memory_type": "semantic",
                "score": 0.88,
                "timestamp": "2026-04-12T15:30:00Z",
                "source": "design_notes",
            },
        ]
        
        # 应用过滤器
        if filters:
            if "memory_type" in filters:
                mock_results = [r for r in mock_results if r["memory_type"] == filters["memory_type"]]
            if "session_id" in filters:
                mock_results = [r for r in mock_results if r.get("session_id") == filters["session_id"]]
        
        # 应用阈值
        results = [r for r in mock_results if r["score"] >= threshold]
        
        result["results"] = results[:limit]
        result["count"] = len(result["results"])
        result["success"] = True
        
    except Exception as e:
        result["error"] = f"Recall error: {str(e)}"
    
    return result