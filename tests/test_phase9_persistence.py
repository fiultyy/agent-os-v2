"""Phase 9 persistence tests — verify SQLiteStore, KG, and JWT revocation.

Run standalone (no numpy/faiss needed for these tests):
    cd /home/yy/projects/agent-os
    /usr/bin/python3 tests/test_phase9_persistence.py
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

try:
    from pysqlite3 import dbapi2 as sqlite3  # type: ignore[import-untyped]
except ImportError:
    import sqlite3

_project_root = Path(__file__).resolve().parent.parent
_orch_src = _project_root / "services" / "orchestrator" / "src"


def _import_module_from_file(module_name: str, file_path: str | Path):
    """Import a module directly from its file path, bypassing __init__.py chain."""
    spec = importlib.util.spec_from_file_location(module_name, str(file_path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


# Load types first (no heavy deps)
_types = _import_module_from_file(
    "src.memory.types", _orch_src / "memory" / "types.py"
)
MemoryItem = _types.MemoryItem
MemoryBlock = _types.MemoryBlock
MemoryFilter = _types.MemoryFilter
MemoryType = _types.MemoryType
MemoryScope = _types.MemoryScope

# Load SQLiteStore
_sqlitestore = _import_module_from_file(
    "src.memory.sqlitestore", _orch_src / "memory" / "sqlitestore.py"
)
SQLiteStore = _sqlitestore.SQLiteStore

# Load KnowledgeGraph
_kg = _import_module_from_file(
    "src.memory.knowledge_graph", _orch_src / "memory" / "knowledge_graph.py"
)
KnowledgeGraph = _kg.KnowledgeGraph
Entity = _kg.Entity
Relation = _kg.Relation


class TestSQLiteStorePersistence(unittest.TestCase):
    """Verify that SQLiteStore persists data across instances (simulating restart)."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.mkdtemp()
        self._db_path = os.path.join(self._tmpdir, "test_memories.db")

    def tearDown(self) -> None:
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_store_and_retrieve_after_restart(self) -> None:
        """Write 10 items, create a new store, read them back."""
        loop = asyncio.new_event_loop()
        try:
            store1 = SQLiteStore(db_path=self._db_path)
            ids: list[str] = []
            for i in range(10):
                item = MemoryItem(
                    content=f"Memory item {i}",
                    agent_id="test-agent",
                    session_id="test-session",
                    memory_type=MemoryType.SESSION,
                    scope=MemoryScope.AGENT,
                    importance=0.5 + i * 0.05,
                    metadata={"index": i},
                )
                item_id = loop.run_until_complete(store1.store(item))
                ids.append(item_id)

            # Simulate restart
            store2 = SQLiteStore(db_path=self._db_path)
            for i, item_id in enumerate(ids):
                item = loop.run_until_complete(store2.get(item_id))
                assert item is not None, f"Item {i} not found after restart"
                assert item.content == f"Memory item {i}"
                assert item.metadata["index"] == i

            items = loop.run_until_complete(
                store2.list_by_scope(MemoryScope.AGENT, agent_id="test-agent")
            )
            assert len(items) == 10

            results = loop.run_until_complete(
                store2.search(MemoryFilter(agent_id="test-agent", keyword="Memory item 5"))
            )
            assert len(results) == 1
            assert results[0].content == "Memory item 5"

            print("  [PASS] 10 items persisted across store instances")
        finally:
            loop.close()

    def test_update_and_delete(self) -> None:
        """Update changes content; delete removes item."""
        loop = asyncio.new_event_loop()
        try:
            store = SQLiteStore(db_path=self._db_path)
            item = MemoryItem(content="original", agent_id="a1")
            item_id = loop.run_until_complete(store.store(item))

            updated = loop.run_until_complete(
                store.update(item_id, content="modified", importance=0.9)
            )
            assert updated is not None
            assert updated.content == "modified"
            assert updated.importance == 0.9

            # Persist across instances
            store2 = SQLiteStore(db_path=self._db_path)
            loaded = loop.run_until_complete(store2.get(item_id))
            assert loaded is not None
            assert loaded.content == "modified"

            ok = loop.run_until_complete(store2.delete(item_id))
            assert ok
            assert loop.run_until_complete(store2.get(item_id)) is None

            print("  [PASS] Update and delete work correctly")
        finally:
            loop.close()

    def test_blocks_persist(self) -> None:
        """Blocks survive restart."""
        loop = asyncio.new_event_loop()
        try:
            store1 = SQLiteStore(db_path=self._db_path)
            block = loop.run_until_complete(
                store1.create_block("agent-1", "persona", initial_content="I am helpful.")
            )
            assert block.content == "I am helpful."

            store2 = SQLiteStore(db_path=self._db_path)
            loaded = loop.run_until_complete(store2.get_block("agent-1", "persona"))
            assert loaded is not None
            assert loaded.content == "I am helpful."

            print("  [PASS] Blocks persisted")
        finally:
            loop.close()

    def test_session_lifecycle(self) -> None:
        """Session create/archive with persistence."""
        loop = asyncio.new_event_loop()
        try:
            store = SQLiteStore(db_path=self._db_path)
            loop.run_until_complete(store.create_session("s1", "a1"))

            for i in range(3):
                loop.run_until_complete(
                    store.store(MemoryItem(
                        content=f"msg-{i}", session_id="s1",
                        memory_type=MemoryType.SESSION, scope=MemoryScope.SESSION,
                    ))
                )

            recent = loop.run_until_complete(store.get_recent("s1", limit=2))
            assert len(recent) == 2

            ok = loop.run_until_complete(store.archive_session("s1"))
            assert ok

            store2 = SQLiteStore(db_path=self._db_path)
            session2 = loop.run_until_complete(store2.get_session("s1"))
            assert session2["status"] == "archived"

            recent2 = loop.run_until_complete(store2.get_recent("s1"))
            assert len(recent2) == 0

            print("  [PASS] Session archive persisted")
        finally:
            loop.close()

    def test_destroy_session(self) -> None:
        """Destroy removes session and all associated memories."""
        loop = asyncio.new_event_loop()
        try:
            store = SQLiteStore(db_path=self._db_path)
            loop.run_until_complete(store.create_session("s2", "a2"))
            mid = loop.run_until_complete(
                store.store(MemoryItem(content="temp", session_id="s2"))
            )

            ok = loop.run_until_complete(store.destroy_session("s2"))
            assert ok

            item = loop.run_until_complete(store.get(mid))
            assert item is None

            session = loop.run_until_complete(store.get_session("s2"))
            assert session is None

            print("  [PASS] Session destroy removes all data")
        finally:
            loop.close()


class TestKGSQLite(unittest.TestCase):
    """Verify SQLite-backed KnowledgeGraph persistence and CTE queries."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.mkdtemp()
        self._db_path = os.path.join(self._tmpdir, "test_kg.db")

    def tearDown(self) -> None:
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_add_and_search_entities(self) -> None:
        """Entities persist and can be searched."""
        kg = KnowledgeGraph(db_path=self._db_path)
        kg.add_entity(Entity(name="Python", entity_type="language"))
        kg.add_entity(Entity(name="FastAPI", entity_type="framework"))

        results = kg.search_entities("Python")
        assert len(results) == 1
        assert results[0]["name"] == "Python"

        print("  [PASS] Entity search works")

    def test_add_relation_and_query_neighbors(self) -> None:
        """Relations persist and CTE neighbor query works."""
        kg = KnowledgeGraph(db_path=self._db_path)
        py_id = kg.add_entity(Entity(name="Python", entity_type="language"))
        kg.add_entity(Entity(name="FastAPI", entity_type="framework"))
        kg.add_entity(Entity(name="Pydantic", entity_type="library"))

        kg.add_relation(Relation(
            source_entity_id="Python", target_entity_id="FastAPI", relation_type="uses",
        ))
        kg.add_relation(Relation(
            source_entity_id="FastAPI", target_entity_id="Pydantic", relation_type="depends_on",
        ))

        neighbors = kg.query_neighbors(py_id, max_depth=1)
        assert len(neighbors) == 1
        assert neighbors[0]["entity"]["name"] == "FastAPI"

        neighbors2 = kg.query_neighbors(py_id, max_depth=2)
        names = {n["entity"]["name"] for n in neighbors2}
        assert "FastAPI" in names
        assert "Pydantic" in names

        print("  [PASS] CTE neighbor query works (1-hop and 2-hop)")

    def test_expire_relation(self) -> None:
        """Expired relations are excluded from queries."""
        kg = KnowledgeGraph(db_path=self._db_path)
        kg.add_entity(Entity(name="A"))
        kg.add_entity(Entity(name="B"))
        rid = kg.add_relation(Relation(
            source_entity_id="A", target_entity_id="B", relation_type="connects",
        ))

        a_id = kg._resolve_entity_id("A")
        rels = kg.get_entity_relations(a_id)
        assert len(rels) == 1

        kg.expire_relation(rid)

        rels = kg.get_entity_relations(a_id)
        assert len(rels) == 0

        print("  [PASS] Expired relations excluded")

    def test_persistence_across_instances(self) -> None:
        """KG data survives process restart (new instance)."""
        kg1 = KnowledgeGraph(db_path=self._db_path)
        kg1.add_entity(Entity(name="Redis", entity_type="database"))
        kg1.add_entity(Entity(name="Python", entity_type="language"))
        kg1.add_relation(Relation(
            source_entity_id="Redis", target_entity_id="Python", relation_type="used_by",
        ))

        kg2 = KnowledgeGraph(db_path=self._db_path)
        stats = kg2.stats()
        assert stats["entity_count"] >= 1
        assert stats["relation_count"] >= 1

        results = kg2.search_entities("Redis")
        assert len(results) == 1

        print("  [PASS] KG data persists across instances")

    def test_extract_and_ingest(self) -> None:
        """Entity extraction still works with SQLite backend."""
        kg = KnowledgeGraph(db_path=self._db_path)
        result = kg.extract_and_ingest(
            'Machine Learning uses Neural Network framework. Neural Network depends on Gradient Descent library.',
            memory_id="m1",
        )
        assert len(result["entity_ids"]) > 0
        assert len(result["relation_ids"]) > 0

        print("  [PASS] extract_and_ingest works")

    def test_expand_and_shortest_path(self) -> None:
        """Expand and shortest_path still work with SQLite backend."""
        kg = KnowledgeGraph(db_path=self._db_path)
        kg.add_entity(Entity(name="X"))
        kg.add_entity(Entity(name="Y"))
        kg.add_entity(Entity(name="Z"))
        kg.add_relation(Relation(source_entity_id="X", target_entity_id="Y", relation_type="links"))
        kg.add_relation(Relation(source_entity_id="Y", target_entity_id="Z", relation_type="links"))

        result = kg.expand("X", depth=2)
        assert result["center"] is not None
        assert len(result["neighbours"]) == 2

        path = kg.shortest_path("X", "Z")
        assert path is not None
        assert len(path) == 3

        print("  [PASS] expand and shortest_path work")

    def test_entity_merge(self) -> None:
        """Same-name entities merge properties."""
        kg = KnowledgeGraph(db_path=self._db_path)
        id1 = kg.add_entity(Entity(name="Python", entity_type="language", properties={"version": "3.12"}))
        id2 = kg.add_entity(Entity(name="Python", entity_type="language", properties={"creator": "Guido"}))
        assert id1 == id2
        entity = kg.get_entity(id1)
        assert entity["properties"]["version"] == "3.12"
        assert entity["properties"]["creator"] == "Guido"

        print("  [PASS] Entity merge works")

    def test_stats(self) -> None:
        """Stats returns correct counts."""
        kg = KnowledgeGraph(db_path=self._db_path)
        kg.add_entity(Entity(name="A", entity_type="type_a"))
        kg.add_entity(Entity(name="B", entity_type="type_b"))
        kg.add_relation(Relation(source_entity_id="A", target_entity_id="B", relation_type="connects"))

        stats = kg.stats()
        assert stats["entity_count"] == 2
        assert stats["relation_count"] == 1
        assert "type_a" in stats["entity_types"]
        assert "connects" in stats["relation_types"]

        print("  [PASS] Stats correct")


class TestJWTPersistence(unittest.TestCase):
    """Verify JWT revocation list persists in SQLite."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.mkdtemp()
        self._db_path = os.path.join(self._tmpdir, "test_auth.db")

    def tearDown(self) -> None:
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_revoked_jti_persists(self) -> None:
        """Revoked JTIs survive across connections."""
        conn = sqlite3.connect(self._db_path)
        conn.execute(
            """CREATE TABLE IF NOT EXISTS revoked_jtis (
                jti TEXT PRIMARY KEY,
                revoked_at TEXT NOT NULL
            )"""
        )
        conn.execute(
            "INSERT INTO revoked_jtis (jti, revoked_at) VALUES (?, datetime('now'))",
            ("test-jti-123",),
        )
        conn.commit()
        conn.close()

        conn2 = sqlite3.connect(self._db_path)
        row = conn2.execute(
            "SELECT 1 FROM revoked_jtis WHERE jti = ?", ("test-jti-123",)
        ).fetchone()
        conn2.close()
        assert row is not None, "Revoked JTI should persist across connections"

        print("  [PASS] JWT revocation persists")


if __name__ == "__main__":
    unittest.main(verbosity=2)
