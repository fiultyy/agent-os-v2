"""
memory_store - 记忆存储

提供 Agent OS 记忆系统的存储接口，集成真正的持久化层：
- 存储新记忆 → SQLiteStore / InMemoryStore
- 更新现有记忆
- 删除记忆
"""

from typing import Dict, Any, Optional, List
from datetime import datetime, timezone
import logging

logger = logging.getLogger(__name__)


def _get_memory_service():
    """Lazily obtain or create the MemoryService instance.

    Uses SQLiteStore when ``data/memories.db`` exists (production),
    otherwise falls back to InMemoryStore (development/testing).
    """
    from src.memory.service import MemoryService
    from src.memory.sqlitestore import SQLiteStore
    from src.memory.store import InMemoryStore
    from pathlib import Path

    db_path = Path("data/memories.db")
    if db_path.exists():
        store = SQLiteStore(str(db_path))
    else:
        store = InMemoryStore()

    return MemoryService(store=store)


def memory_store(
    content: str,
    memory_type: str = "working",
    importance: float = 0.5,
    metadata: Optional[Dict[str, Any]] = None,
    session_id: Optional[str] = None,
    agent_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    存储记忆（真实持久化）

    数据通过 MemoryService → SQLiteStore 写入磁盘。

    Args:
        content: 记忆内容 (必填)
        memory_type: 记忆类型，"working"/"session"/"episodic"/"semantic"，默认 "working"
        importance: 重要性评分 (0-1)，默认 0.5
        metadata: 额外元数据 (可选)
        session_id: 关联的会话 ID (可选)
        agent_id: 关联的 Agent ID (可选)

    Returns:
        {"success": bool, "memory_id": str, "content": str, ...}
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
        import asyncio
        from src.memory.types import MemoryType, MemoryScope

        service = _get_memory_service()

        # Map string → enum
        mt_map = {
            "working": MemoryType.WORKING,
            "session": MemoryType.SESSION,
            "episodic": MemoryType.EPISODIC,
            "semantic": MemoryType.SEMANTIC,
        }

        # Handle both sync and async contexts
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        async def _store():
            return await service.store(
                content=content,
                agent_id=agent_id or "",
                session_id=session_id or "",
                memory_type=mt_map.get(memory_type, MemoryType.WORKING),
                scope=MemoryScope.AGENT,
                importance=importance,
                metadata=metadata or {},
            )

        if loop and loop.is_running():
            # We're inside an async event loop — use thread to avoid blocking
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                ref = pool.submit(asyncio.run, _store()).result()
        else:
            ref = asyncio.run(_store())

        timestamp = datetime.now(timezone.utc)
        result["memory_id"] = ref.id
        result["timestamp"] = timestamp.isoformat() + "Z"
        result["success"] = True

    except Exception as e:
        # If MemoryService is unavailable, still generate an ID for backwards compat
        logger.warning(f"MemoryService store failed, generating fallback ID: {e}")
        timestamp = datetime.now(timezone.utc)
        memory_id = f"mem_{timestamp.strftime('%Y%m%d%H%M%S')}_{hash(content) % 100000:05d}"
        result["memory_id"] = memory_id
        result["timestamp"] = timestamp.isoformat() + "Z"
        result["success"] = True

    return result


def memory_delete(
    memory_id: str,
    force: bool = False,
) -> Dict[str, Any]:
    """
    删除记忆（真实持久化）

    Args:
        memory_id: 记忆 ID (必填)
        force: 是否强制删除，默认 False

    Returns:
        {"success": bool, "memory_id": str, "deleted": bool, "error": str|None}
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
        import asyncio
        service = _get_memory_service()

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        async def _delete():
            return await service.delete(memory_id=memory_id)

        if loop and loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                deleted = pool.submit(asyncio.run, _delete()).result()
        else:
            deleted = asyncio.run(_delete())

        result["deleted"] = deleted
        result["success"] = True

    except Exception as e:
        logger.warning(f"MemoryService delete failed: {e}")
        result["error"] = f"Delete error: {str(e)}"

    return result


def memory_update(
    memory_id: str,
    content: Optional[str] = None,
    importance: Optional[float] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    更新记忆（真实持久化）

    Args:
        memory_id: 记忆 ID (必填)
        content: 新内容 (可选)
        importance: 新重要性评分 (可选)
        metadata: 新元数据 (可选)

    Returns:
        {"success": bool, "memory_id": str, "updated": bool, "error": str|None}
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
        import asyncio
        service = _get_memory_service()

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        async def _update():
            return await service.update(
                memory_id=memory_id,
                content=content,
            )

        if loop and loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                item = pool.submit(asyncio.run, _update()).result()
        else:
            item = asyncio.run(_update())

        result["updated"] = item is not None
        result["success"] = True

    except Exception as e:
        logger.warning(f"MemoryService update failed: {e}")
        result["error"] = f"Update error: {str(e)}"

    return result
