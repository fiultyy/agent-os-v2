"""PitFail API routes — read-side query surface over PitfailRegistry.

通电只读端点(挂载于 engine /v1 前缀):
- GET /v1/pitfall/            — list_all(limit)
- GET /v1/pitfall/search      — search(query, limit)
- GET /v1/pitfall/match       — match(file_path, error_type)
- GET /v1/pitfall/{pitfall_id}— get(pitfall_id)

写入路径是工具失败 hook(chat.py _node_tool),不经此 API —— 这里纯查询。当
``_state.pitfail_registry is None``(装配降级 / 单测环境未挂载)时,各端点返回
安全空载({"disabled": true} 或空列表),与 pg_store/knowledge_graph 的 None-guard
约定一致(零回归)。
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from src.services import _state

router = APIRouter()


def _record_to_dict(rec) -> dict:
    """PitfallRecord → JSON-safe dict(datetime 转 ISO 字符串)。"""
    return {
        "id": rec.id,
        "file_path": rec.file_path,
        "error_type": rec.error_type,
        "symptom": rec.symptom,
        "root_cause": rec.root_cause,
        "fix": rec.fix,
        "project_id": getattr(rec, "project_id", "default"),
        "created_at": rec.created_at.isoformat() if rec.created_at else None,
        "updated_at": rec.updated_at.isoformat() if rec.updated_at else None,
        "recurrence_count": rec.recurrence_count,
        "tags": list(rec.tags) if rec.tags else [],
        "llm_summary": rec.llm_summary,
    }


@router.get("/pitfall/")
async def list_pitfalls(limit: int = Query(default=100, le=500)) -> dict | list:
    """List all pitfall records (newest-updated first)."""
    if _state.pitfail_registry is None:
        return {"disabled": True, "items": []}
    return [_record_to_dict(r) for r in _state.pitfail_registry.list_all(limit=limit)]


@router.get("/pitfall/search")
async def search_pitfalls(
    query: str = Query(..., min_length=1),
    limit: int = Query(default=5, le=100),
) -> dict | list:
    """Search pitfalls by symptom/root_cause/fix (LIKE)."""
    if _state.pitfail_registry is None:
        return {"disabled": True, "items": []}
    return [_record_to_dict(r) for r in _state.pitfail_registry.search(query, limit=limit)]


@router.get("/pitfall/match")
async def match_pitfall(
    file_path: str = Query(...),
    error_type: str = Query(...),
) -> dict | list:
    """Match pitfalls by exact (file_path, error_type)."""
    if _state.pitfail_registry is None:
        return {"disabled": True, "items": []}
    return [_record_to_dict(r) for r in _state.pitfail_registry.match(file_path, error_type)]


@router.get("/pitfall/{pitfall_id}")
async def get_pitfall(pitfall_id: str) -> dict:
    """Get a single pitfall record by id."""
    if _state.pitfail_registry is None:
        return {"disabled": True}
    rec = _state.pitfail_registry.get(pitfall_id)
    if rec is None:
        return {"error": "not found", "id": pitfall_id}
    return _record_to_dict(rec)
