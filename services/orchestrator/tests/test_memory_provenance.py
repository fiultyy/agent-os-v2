"""Integration tests for P0 memory provenance (origin field).

Verifies the core P0 guarantee: autonomous consolidation paths
(forgetting / reflect / migrator / dreamer-via-reflect) only process
``origin=AGENT`` memories. FOREGROUND (user- or system-entered) memories
are never auto-forgotten, merged, or migrated.

Covers:
- store() default vs explicit origin (backward compat).
- forgetting.run_sweep archives AGENT but never FOREGROUND.
- reflect merges only AGENT episodic; FOREGROUND episodic untouched.
- migrator promotes only AGENT session; FOREGROUND untouched.
- store.search MemoryFilter(origin=...) filters in InMemory + SQLite.
- SQLite legacy DB (no origin column) is backfilled to 'foreground'.
- All consolidation outputs are tagged origin=AGENT.
"""

import os
import sys
import tempfile

try:
    from pysqlite3 import dbapi2 as sqlite3  # consistent with sqlitestore.py
except ImportError:
    import sqlite3  # noqa: F401 — stdlib fallback
from datetime import datetime, timezone, timedelta

import pytest

# Ensure src is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.memory.types import (
    MemoryItem, MemoryFilter, MemoryType, MemoryOrigin,
)
from src.memory.store import InMemoryStore
from src.memory.sqlitestore import SQLiteStore
from src.memory.service import MemoryService
from src.memory.scorer import ImportanceScorer
from src.memory.forgetting import ActiveForgetting
from src.memory.migrator import MemoryMigrator


def _svc(store=None) -> MemoryService:
    return MemoryService(store=store or InMemoryStore())


def _days_ago(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


# ── 1. store origin: default FOREGROUND, explicit AGENT ─────────────

class TestStoreOrigin:
    @pytest.mark.asyncio
    async def test_default_origin_is_foreground(self):
        svc = _svc()
        ref = await svc.store(content="user note", agent_id="a1")
        item = await svc.get(ref.id)
        assert item.origin == MemoryOrigin.FOREGROUND

    @pytest.mark.asyncio
    async def test_explicit_agent_origin(self):
        svc = _svc()
        ref = await svc.store(
            content="agent note", agent_id="a1",
            origin=MemoryOrigin.AGENT,
        )
        item = await svc.get(ref.id)
        assert item.origin == MemoryOrigin.AGENT

    @pytest.mark.asyncio
    async def test_foreground_protected_on_silent_default(self):
        """Existing callers that don't pass origin get FOREGROUND
        (the protective default) — no silent agent-sediment."""
        svc = _svc()
        ref = await svc.store(content="legacy call", agent_id="a1")
        assert (await svc.get(ref.id)).origin == MemoryOrigin.FOREGROUND


# ── 2. forgetting never archives FOREGROUND ─────────────────────────

class TestForgettingProvenance:
    @pytest.mark.asyncio
    async def test_foreground_protected_agent_archived(self):
        """Identical low-score aged memories differ only by origin;
        only the AGENT one is archived."""
        svc = _svc()
        scorer = ImportanceScorer(forget_threshold=0.15)
        forgetting = ActiveForgetting(svc, scorer=scorer, min_age_hours=0)

        old = _days_ago(30)
        fg_ref = await svc.store(
            content=" mundane observation about nothing ",
            agent_id="a1", importance=0.01,
            origin=MemoryOrigin.FOREGROUND,
        )
        ag_ref = await svc.store(
            content=" mundane observation about nothing ",
            agent_id="a1", importance=0.01,
            origin=MemoryOrigin.AGENT,
        )
        # InMemoryStore.get returns a live reference; mutate created_at in place
        (await svc.get(fg_ref.id)).created_at = old
        (await svc.get(ag_ref.id)).created_at = old

        result = await forgetting.run_sweep(agent_id="a1")

        assert (await svc.get(fg_ref.id)).archived is False
        assert (await svc.get(ag_ref.id)).archived is True
        assert ag_ref.id in result.archived_ids
        assert fg_ref.id not in result.archived_ids


# ── 3. reflect merges only AGENT episodic ────────────────────────────

class TestReflectProvenance:
    @pytest.mark.asyncio
    async def test_foreground_episodic_not_merged(self):
        svc = _svc()
        # High-overlap AGENT episodic pair (will be merged into SEMANTIC)
        await svc.store(
            content="deploy the api service",
            agent_id="a1", memory_type=MemoryType.EPISODIC,
            origin=MemoryOrigin.AGENT,
        )
        await svc.store(
            content="deploy the api service now",
            agent_id="a1", memory_type=MemoryType.EPISODIC,
            origin=MemoryOrigin.AGENT,
        )
        # FOREGROUND episodic with identical content — must NOT be merged
        fg_ref = await svc.store(
            content="deploy the api service",
            agent_id="a1", memory_type=MemoryType.EPISODIC,
            origin=MemoryOrigin.FOREGROUND,
        )

        await svc.reflect(agent_id="a1", trigger="test", top_k=20)

        sem = await svc._store.search(MemoryFilter(
            agent_id="a1", memory_type=MemoryType.SEMANTIC))
        assert len(sem) >= 1
        # Consolidation output is agent self-sediment
        assert all(s.origin == MemoryOrigin.AGENT for s in sem)

        # FOREGROUND episodic untouched: still episodic, original origin
        fg_item = await svc.get(fg_ref.id)
        assert fg_item.memory_type == MemoryType.EPISODIC
        assert fg_item.origin == MemoryOrigin.FOREGROUND


# ── 4. migrator promotes only AGENT memories ────────────────────────

class TestMigratorProvenance:
    @pytest.mark.asyncio
    async def test_working_to_session_marks_agent(self):
        svc = _svc()
        migrator = MemoryMigrator(svc)
        item = MemoryItem(
            content="working note about deployment",
            memory_type=MemoryType.WORKING,
        )
        sid = await migrator.migrate_working_to_session(item, "s1", "a1")
        stored = await svc.get(sid)
        assert stored is not None
        assert stored.memory_type == MemoryType.SESSION
        assert stored.origin == MemoryOrigin.AGENT

    @pytest.mark.asyncio
    async def test_foreground_session_not_promoted(self):
        svc = _svc()
        migrator = MemoryMigrator(svc)
        await svc.store(
            content="agent session work item",
            agent_id="a1", session_id="s1",
            memory_type=MemoryType.SESSION,
            origin=MemoryOrigin.AGENT,
        )
        fg_ref = await svc.store(
            content="user session note",
            agent_id="a1", session_id="s1",
            memory_type=MemoryType.SESSION,
            origin=MemoryOrigin.FOREGROUND,
        )

        epi_ids = await migrator.migrate_session_to_episodic("s1", "a1")

        assert len(epi_ids) >= 1
        for eid in epi_ids:
            item = await svc.get(eid)
            assert item.memory_type == MemoryType.EPISODIC
            assert item.origin == MemoryOrigin.AGENT

        # FOREGROUND session memory left in place, unmodified
        fg_item = await svc.get(fg_ref.id)
        assert fg_item.memory_type == MemoryType.SESSION
        assert fg_item.origin == MemoryOrigin.FOREGROUND
        assert fg_item.content == "user session note"


# ── 5. store.search origin filter (InMemory + SQLite) ───────────────

class TestStoreSearchOrigin:
    @pytest.mark.asyncio
    async def test_inmemory_search_origin_filter(self):
        store = InMemoryStore()
        await store.store(MemoryItem(
            id="fg1", content="fg", agent_id="a",
            origin=MemoryOrigin.FOREGROUND,
        ))
        await store.store(MemoryItem(
            id="ag1", content="ag", agent_id="a",
            origin=MemoryOrigin.AGENT,
        ))

        fg = await store.search(MemoryFilter(agent_id="a", origin=MemoryOrigin.FOREGROUND))
        ag = await store.search(MemoryFilter(agent_id="a", origin=MemoryOrigin.AGENT))
        all_items = await store.search(MemoryFilter(agent_id="a"))
        assert len(fg) == 1 and fg[0].origin == MemoryOrigin.FOREGROUND
        assert len(ag) == 1 and ag[0].origin == MemoryOrigin.AGENT
        assert len(all_items) == 2

    @pytest.mark.asyncio
    async def test_sqlite_search_origin_filter(self):
        with tempfile.TemporaryDirectory() as d:
            store = SQLiteStore(os.path.join(d, "t.db"))
            await store.store(MemoryItem(
                id="fg1", content="fg", agent_id="a",
                origin=MemoryOrigin.FOREGROUND,
            ))
            await store.store(MemoryItem(
                id="ag1", content="ag", agent_id="a",
                origin=MemoryOrigin.AGENT,
            ))
            ag = await store.search(MemoryFilter(agent_id="a", origin=MemoryOrigin.AGENT))
            fg = await store.search(MemoryFilter(agent_id="a", origin=MemoryOrigin.FOREGROUND))
            assert len(ag) == 1 and ag[0].origin == MemoryOrigin.AGENT
            assert len(fg) == 1 and fg[0].origin == MemoryOrigin.FOREGROUND


# ── 6. SQLite legacy DB migration (ALTER backfill) ──────────────────

class TestSQLiteLegacyMigration:
    @pytest.mark.asyncio
    async def test_legacy_db_backfills_origin_foreground(self):
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "t.db")
            # Build a legacy memories table WITHOUT the origin column
            conn = sqlite3.connect(db)
            conn.execute(
                """CREATE TABLE memories (
                    id TEXT PRIMARY KEY, content TEXT NOT NULL, scope TEXT,
                    memory_type TEXT, importance REAL DEFAULT 0.0, metadata TEXT,
                    agent_id TEXT DEFAULT '', session_id TEXT DEFAULT '',
                    created_at TEXT, accessed_at TEXT, updated_at TEXT,
                    archived INTEGER DEFAULT 0
                )"""
            )
            conn.execute(
                "INSERT INTO memories (id, content, memory_type, agent_id, "
                "created_at, accessed_at) VALUES (?, ?, ?, ?, ?, ?)",
                ("legacy-1", "old user memory", "session", "a",
                 "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"),
            )
            conn.commit()
            conn.close()

            # Opening with SQLiteStore must ALTER-add origin, backfill foreground
            store = SQLiteStore(db)
            cols = [r[1] for r in store._conn.execute(
                "PRAGMA table_info(memories)").fetchall()]
            assert "origin" in cols

            legacy = await store.get("legacy-1")
            assert legacy is not None
            assert legacy.origin == MemoryOrigin.FOREGROUND
