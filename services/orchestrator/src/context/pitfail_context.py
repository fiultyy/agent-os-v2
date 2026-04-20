"""PitfailContextBuilder — L3: PitFail 上下文构建。

从 PitfailRegistry 查询相关的踩坑记录，
供 CAContextCoding.layer3.pitfail_references 使用。
"""

from __future__ import annotations

from typing import Any

from ..pitfail.models import PitfallRecord
from ..pitfail.registry import PitfailRegistry


class PitfailContextBuilder:
    """L3: PitFail 上下文构建器 — 与 PitfailRegistry 集成。"""

    def __init__(self, registry: PitfailRegistry) -> None:
        self.registry = registry

    def get_relevant_pitfalls(
        self,
        file_paths: list[str],
        error_types: list[str] | None = None,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """获取与给定文件和错误类型相关的 PitFail 记录。

        Args:
            file_paths: 当前涉及的文件路径列表。
            error_types: 错误类型列表（默认常见类型）。
            limit: 返回上限。

        Returns:
            去重后的 PitFail 记录列表（dict 形式），按 recurrence_count 降序。
        """
        if error_types is None:
            error_types = ["import_error", "syntax_error", "runtime_error"]

        seen_ids: set[str] = set()
        results: list[PitfallRecord] = []

        for fp in file_paths:
            for et in error_types:
                matches = self.registry.match(fp, et)
                for m in matches:
                    if m.id and m.id not in seen_ids:
                        seen_ids.add(m.id)
                        results.append(m)

        # Sort by recurrence_count descending, then limit
        results.sort(key=lambda x: x.recurrence_count, reverse=True)
        results = results[:limit]

        # Convert to dict for context injection
        return [
            {
                "id": r.id,
                "file_path": r.file_path,
                "error_type": r.error_type,
                "symptom": r.symptom,
                "root_cause": r.root_cause,
                "fix": r.fix,
                "recurrence_count": r.recurrence_count,
            }
            for r in results
        ]
