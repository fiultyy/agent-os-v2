# services/orchestrator/src/pitfail/registry.py
import sqlite3
import json
from pathlib import Path
from datetime import datetime, UTC
from typing import Optional, List
from .models import PitfallRecord
import uuid

class PitfailRegistry:
    def __init__(self, db_path: str = "data/pitfalls.db"):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS pitfalls (
                    id TEXT PRIMARY KEY,
                    file_path TEXT NOT NULL,
                    error_type TEXT NOT NULL,
                    symptom TEXT NOT NULL,
                    root_cause TEXT NOT NULL,
                    fix TEXT NOT NULL,
                    project_id TEXT DEFAULT 'default',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    recurrence_count INTEGER DEFAULT 1,
                    tags TEXT DEFAULT '[]',
                    llm_summary TEXT
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_file_error ON pitfalls(file_path, error_type)")

    def _row_to_record(self, row) -> PitfallRecord:
        return PitfallRecord(
            id=row[0], file_path=row[1], error_type=row[2],
            symptom=row[3], root_cause=row[4], fix=row[5],
            project_id=row[6], created_at=datetime.fromisoformat(row[7]),
            updated_at=datetime.fromisoformat(row[8]),
            recurrence_count=row[9], tags=json.loads(row[10]), llm_summary=row[11]
        )

    def record(self, pitfall: PitfallRecord) -> str:
        pitfall.id = pitfall.id or str(uuid.uuid4())
        pitfall.created_at = datetime.now(UTC)
        pitfall.updated_at = datetime.now(UTC)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT INTO pitfalls (id, file_path, error_type, symptom, root_cause, fix, project_id, created_at, updated_at, recurrence_count, tags, llm_summary)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (pitfall.id, pitfall.file_path, pitfall.error_type, pitfall.symptom, pitfall.root_cause, pitfall.fix, pitfall.project_id, pitfall.created_at.isoformat(), pitfall.updated_at.isoformat(), pitfall.recurrence_count, json.dumps(pitfall.tags), pitfall.llm_summary))
        return pitfall.id

    def search(self, query: str, limit: int = 5) -> List[PitfallRecord]:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                SELECT * FROM pitfalls
                WHERE symptom LIKE ? OR root_cause LIKE ? OR fix LIKE ?
                ORDER BY recurrence_count DESC
                LIMIT ?
            """, (f"%{query}%", f"%{query}%", f"%{query}%", limit))
            return [self._row_to_record(row) for row in cursor.fetchall()]

    def match(self, file_path: str, error_type: str) -> List[PitfallRecord]:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                SELECT * FROM pitfalls
                WHERE file_path = ? AND error_type = ?
                ORDER BY recurrence_count DESC
            """, (file_path, error_type))
            return [self._row_to_record(row) for row in cursor.fetchall()]

    def get(self, pitfall_id: str) -> Optional[PitfallRecord]:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("SELECT * FROM pitfalls WHERE id = ?", (pitfall_id,))
            row = cursor.fetchone()
            return self._row_to_record(row) if row else None

    def increment_recurrence(self, pitfall_id: str) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                UPDATE pitfalls
                SET recurrence_count = recurrence_count + 1, updated_at = ?
                WHERE id = ?
            """, (datetime.now(UTC).isoformat(), pitfall_id))

    def list_all(self, limit: int = 100) -> List[PitfallRecord]:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("SELECT * FROM pitfalls ORDER BY updated_at DESC LIMIT ?", (limit,))
            return [self._row_to_record(row) for row in cursor.fetchall()]
