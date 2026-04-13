"""
memory_store - 记忆存储

提供 Agent OS 记忆系统的存储接口：
- 存储新记忆
- 更新现有记忆
- 删除记忆
"""

from typing import Dict, Any, Optional, List
from datetime import datetime, timezone


def memory_store(
    content: str,
    memory_type: str = "working",
    importance: float = 0.5,
    metadata: Optional[Dict[str, Any]] = None,
    session_id: Optional[str] = None,
    agent_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    存储记忆
    
    Args:
        content: 记忆内容 (必填)
        memory_type: 记忆类型，"working"/"session"/"episodic"/"semantic"，默认 "working"
        importance: 重要性评分 (0-1)，默认 0.5
        metadata: 额外元数据 (可选)
            - tags: 标签列表
            - source: 来源
            - references: 关联引用
        session_id: 关联的会话 ID (可选)
        agent_id: 关联的 Agent ID (可选)
    
    Returns:
        {
            "success": bool,
            "memory_id": str,
            "content": str,
            "memory_type": str,
            "importance": float,
            "timestamp": str,
            "error": str (if failed)
        }
    
    Example:
        >>> result = memory_store(
        ...     content="Completed P3-1: L3.3 primitive tools implemented",
        ...     memory_type="episodic",
        ...     importance=0.8,
        ...     metadata={"tags": ["implementation", "L3.3"], "commit": "09dad8d"}
        ... )
        >>> if result["success"]:
        ...     print(f"Stored: {result['memory_id']}")
        >>>
        >>> # 更新 working memory
        >>> result = memory_store(
        ...     content="Current task: Implement P3-2 skill tools",
        ...     memory_type="working",
        ...     importance=0.9
        ... )
    """
    result = {
        "success": False,
        "memory_id": None,
        "content": content,
        "memory_type": memory_type,
        "importance": importance,
        "timestamp": None,
        "error": None,
    }
    
    if not content:
        result["error"] = "Content is required"
        return result
    
    valid_types = ["working", "session", "episodic", "semantic"]
    if memory_type not in valid_types:
        result["error"] = f"Invalid memory_type: {memory_type}. Must be one of {valid_types}"
        return result
    
    if not 0 <= importance <= 1:
        result["error"] = f"Invalid importance: {importance}. Must be between 0 and 1"
        return result
    
    try:
        # 生成记忆 ID
        timestamp = datetime.now(timezone.utc)
        memory_id = f"mem_{timestamp.strftime('%Y%m%d%H%M%S')}_{hash(content) % 100000:05d}"
        
        result["memory_id"] = memory_id
        result["timestamp"] = timestamp.isoformat() + "Z"
        result["success"] = True
        
        # 实际实现需要调用 Agent OS 的 MemoryService
        # MemoryService.store(content, memory_type, importance, metadata, session_id, agent_id)
        
    except Exception as e:
        result["error"] = f"Store error: {str(e)}"
    
    return result


def memory_delete(
    memory_id: str,
    force: bool = False,
) -> Dict[str, Any]:
    """
    删除记忆
    
    Args:
        memory_id: 记忆 ID (必填)
        force: 是否强制删除（忽略保护状态），默认 False
    
    Returns:
        {
            "success": bool,
            "memory_id": str,
            "deleted": bool,
            "error": str (if failed)
        }
    
    Example:
        >>> result = memory_delete("mem_20260413100000_12345")
        >>> if result["success"] and result["deleted"]:
        ...     print("Memory deleted")
    """
    result = {
        "success": False,
        "memory_id": memory_id,
        "deleted": False,
        "error": None,
    }
    
    if not memory_id:
        result["error"] = "memory_id is required"
        return result
    
    try:
        # 实际实现需要调用 Agent OS 的 MemoryService
        # MemoryService.delete(memory_id, force)
        
        result["deleted"] = True
        result["success"] = True
        
    except Exception as e:
        result["error"] = f"Delete error: {str(e)}"
    
    return result


def memory_update(
    memory_id: str,
    content: Optional[str] = None,
    importance: Optional[float] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    更新记忆
    
    Args:
        memory_id: 记忆 ID (必填)
        content: 新内容 (可选)
        importance: 新重要性评分 (可选)
        metadata: 新元数据 (可选)
    
    Returns:
        {
            "success": bool,
            "memory_id": str,
            "updated": bool,
            "error": str (if failed)
        }
    
    Example:
        >>> result = memory_update("mem_20260413100000_12345", importance=0.9)
        >>> if result["success"]:
        ...     print("Memory updated")
    """
    result = {
        "success": False,
        "memory_id": memory_id,
        "updated": False,
        "error": None,
    }
    
    if not memory_id:
        result["error"] = "memory_id is required"
        return result
    
    try:
        # 实际实现需要调用 Agent OS 的 MemoryService
        # MemoryService.update(memory_id, content, importance, metadata)
        
        result["updated"] = True
        result["success"] = True
        
    except Exception as e:
        result["error"] = f"Update error: {str(e)}"
    
    return result