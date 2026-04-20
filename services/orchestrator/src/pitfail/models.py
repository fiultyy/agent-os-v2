# services/orchestrator/src/pitfail/models.py
from dataclasses import dataclass, field
from datetime import datetime, UTC
from typing import Optional

@dataclass
class PitfallRecord:
    """踩坑记录"""
    id: Optional[str] = None
    file_path: str = ""
    error_type: str = ""
    symptom: str = ""
    root_cause: str = ""
    fix: str = ""
    project_id: str = "default"
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    recurrence_count: int = 1
    tags: list[str] = field(default_factory=list)
    llm_summary: Optional[str] = None
