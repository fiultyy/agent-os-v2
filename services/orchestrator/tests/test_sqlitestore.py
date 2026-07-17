"""SQLiteStore unit-test baseline — core CRUD + edge cases (empty/dup/migrate).

Real SQLite-backed store (InMemoryStore's persistent sibling). Each test gets
a throwaway db via ``tmp_path``; ``conftest`` already swaps in ``pysqlite3``
so no per-test ``sqlite3`` patch is needed. Covers the main public surface to
anchor behaviour — not exhaustive.
"""

import sqlite3

import pytest

from src.memory.sqlitestore import SQLiteStore
from src.memory.types import (
    MemoryFilter,
    MemoryItem,
    MemoryOrigin,
    MemoryScope,
    MemoryState,
)


@pytest.fixture
def store(tmp_path):
    return SQLiteStore(str(tmp_path / "mem.db"))


# ── memory item CRUD ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_store_assigns_id_and_get_roundtrip(store):
    mid = await store.store(MemoryItem(content="hello", scope=MemoryScope.GLOBAL))
    assert mid  # uuid assigned when none provided
    got = await store.get(mid)
    assert got is not None
    assert got.content == "hello"
    assert got.scope == MemoryScope.GLOBAL


@pytest.mark.asyncio
async def test_store_metadata_roundtrip(store):
    mid = await store.store(MemoryItem(content="c", metadata={"k": "v", "n": 1}))
    assert (await store.get(mid)).metadata == {"k": "v", "n": 1}


@pytest.mark.asyncio
async def test_get_missing_returns_none(store):
    assert await store.get("nope") is None


@pytest.mark.asyncio
async def test_update_content_and_field(store):
    mid = await store.store(MemoryItem(content="a", importance=0.1))
    updated = await store.update(mid, content="b", importance=0.9)
    assert updated.content == "b"
    assert updated.importance == 0.9
    assert (await store.get(mid)).content == "b"


@pytest.mark.asyncio
async def test_update_missing_returns_none(store):
    assert await store.update("nope", content="x") is None


@pytest.mark.asyncio
async def test_delete_returns_true_then_false(store):
    mid = await store.store(MemoryItem(content="a"))
    assert await store.delete(mid) is True
    assert await store.get(mid) is None
    assert await store.delete(mid) is False  # already gone


# ── store same id replaces (INSERT OR REPLACE) ──────────────────────

@pytest.mark.asyncio
async def test_store_same_id_replaces(store):
    await store.store(MemoryItem(id="fixed", content="v1"))
    await store.store(MemoryItem(id="fixed", content="v2"))
    assert (await store.get("fixed")).content == "v2"


# ── list_by_scope: filter + agent + limit + archived exclusion ───────

@pytest.mark.asyncio
async def test_list_by_scope_excludes_archived(store):
    await store.store(MemoryItem(content="g1", scope=MemoryScope.GLOBAL))
    archived = MemoryItem(content="g2", scope=MemoryScope.GLOBAL)
    archived.archived = True
    await store.store(archived)
    rows = await store.list_by_scope(MemoryScope.GLOBAL)
    assert {r.content for r in rows} == {"g1"}  # archived excluded


@pytest.mark.asyncio
async def test_list_by_scope_agent_filter_and_limit(store):
    await store.store(MemoryItem(content="a1", scope=MemoryScope.AGENT, agent_id="A"))
    await store.store(MemoryItem(content="a2", scope=MemoryScope.AGENT, agent_id="B"))
    await store.store(MemoryItem(content="a3", scope=MemoryScope.AGENT, agent_id="A"))
    rows = await store.list_by_scope(MemoryScope.AGENT, agent_id="A", limit=1)
    assert len(rows) == 1
    assert all(r.agent_id == "A" for r in rows)


# ── search ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_search_keyword_match(store):
    await store.store(MemoryItem(content="deploy the server"))
    await store.store(MemoryItem(content="unrelated note"))
    rows = await store.search(MemoryFilter(keyword="deploy"))
    assert [r.content for r in rows] == ["deploy the server"]


# ── blocks ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_block_crud_and_char_limit(store):
    blk = await store.create_block("A", "persona", char_limit=10, initial_content="hi")
    assert blk.content == "hi"
    assert (await store.get_block("A", "persona")).content == "hi"

    assert (await store.update_block("A", "persona", "persona!")).content == "persona!"
    with pytest.raises(ValueError):
        await store.update_block("A", "persona", "x" * 11)  # exceeds limit

    assert len(await store.list_blocks("A")) == 1


@pytest.mark.asyncio
async def test_block_missing_returns_none(store):
    assert await store.get_block("nope", "label") is None
    assert await store.update_block("nope", "label", "x") is None


@pytest.mark.asyncio
async def test_create_block_duplicate_raises(store):
    """UNIQUE(agent_id, label) — second create violates the constraint."""
    await store.create_block("A", "persona")
    with pytest.raises(sqlite3.IntegrityError):
        await store.create_block("A", "persona")


# ── sessions ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_session_lifecycle(store):
    sess = await store.create_session("s1", "A")
    assert sess["status"] == "active" and sess["message_count"] == 0

    await store.append_to_session("s1", {})
    await store.append_to_session("s1", {})
    assert (await store.get_session("s1"))["message_count"] == 2

    mid = await store.store(MemoryItem(content="m", session_id="s1"))
    recent = await store.get_recent("s1")
    assert [r.id for r in recent] == [mid]

    assert await store.archive_session("s1") is True
    assert (await store.get_session("s1"))["status"] == "archived"
    assert await store.get_recent("s1") == []  # memories now archived


@pytest.mark.asyncio
async def test_destroy_session_removes_memories(store):
    await store.create_session("s1", "A")
    mid = await store.store(MemoryItem(content="m", session_id="s1"))
    assert await store.destroy_session("s1") is True
    assert await store.get(mid) is None
    assert await store.get_session("s1") is None


@pytest.mark.asyncio
async def test_session_missing_returns_none_and_false(store):
    assert await store.get_session("nope") is None
    assert await store.archive_session("nope") is False
    assert await store.destroy_session("nope") is False


# ── max_updated_at ──────────────────────────────────────────────────

def test_max_updated_at_empty_returns_none(store):
    assert store.max_updated_at() is None


@pytest.mark.asyncio
async def test_max_updated_after_write(store):
    await store.store(MemoryItem(content="a"))
    assert store.max_updated_at() is not None


# ── schema migration: legacy db backfill ────────────────────────────

@pytest.mark.asyncio
async def test_migrate_legacy_db_backfills_state(tmp_path):
    """A pre-P0/P3 db (no origin/state cols) with an archived row must be
    migrated on open: ``state`` column added, ``archived=1`` row backfilled
    to ARCHIVED, missing origin defaults to FOREGROUND."""
    db = tmp_path / "legacy.db"
    raw = sqlite3.connect(str(db))
    raw.execute("""CREATE TABLE memories (
        id TEXT PRIMARY KEY, content TEXT, scope TEXT, memory_type TEXT,
        importance REAL, metadata TEXT, agent_id TEXT DEFAULT '',
        session_id TEXT DEFAULT '', created_at TEXT, accessed_at TEXT,
        updated_at TEXT, archived INTEGER DEFAULT 0)""")
    raw.execute(
        "INSERT INTO memories (id, content, archived) VALUES ('x', 'old', 1)"
    )
    raw.commit()
    raw.close()

    store = SQLiteStore(str(db))  # __init__ runs _migrate_schema
    got = await store.get("x")
    assert got is not None
    assert got.state == MemoryState.ARCHIVED  # backfilled from archived=1
    assert got.archived is True
    assert got.origin == MemoryOrigin.FOREGROUND  # default backfill
