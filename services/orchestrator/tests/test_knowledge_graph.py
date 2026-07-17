"""KnowledgeGraph unit-test baseline — core paths + edge cases.

Three public components in ``src/memory.knowledge_graph``:
- :class:`Entity` / :class:`Relation` — dataclasses (auto id + timestamp).
- :class:`EntityExtractor` — regex entity/relation extraction (EN + CJK).
- :class:`KnowledgeGraph` — SQLite-backed graph store (recursive-CTE traversal).

All methods are **sync** (no ``async def``), so tests need no ``@pytest.mark.asyncio``.
Each ``KnowledgeGraph`` test gets a throwaway db via ``tmp_path``; ``conftest``
already swaps in ``pysqlite3`` so no per-test ``sqlite3`` patch is needed.
Covers the main public surface to anchor behaviour — not exhaustive
(``query_neighbors`` / ``get_entity_relations`` overlap with ``expand`` /
``get_relations`` and are left for a fuller pass).
"""

import pytest

from src.memory.knowledge_graph import (
    Entity,
    EntityExtractor,
    KnowledgeGraph,
    Relation,
)


@pytest.fixture
def kg(tmp_path):
    return KnowledgeGraph(str(tmp_path / "kg.db"))


@pytest.fixture
def extractor():
    return EntityExtractor()


# ── dataclass: auto id + timestamp ───────────────────────────────────

def test_entity_auto_assigns_id_and_timestamp():
    e = Entity(name="X")
    assert e.id and e.created_at  # both auto-filled when omitted
    r = Relation(source_entity_id="a", target_entity_id="b")
    assert r.id and r.created_at


# ── EntityExtractor (pure, no DB) ────────────────────────────────────

def test_extractor_quoted_entity(extractor):
    entities = extractor.extract_entities('the "my term" here')
    assert any(e.name == "my term" and e.entity_type == "quoted_term" for e in entities)


def test_extractor_cjk_quoted_entity(extractor):
    # P0-2 评审重点:中文实体抽取(原纯正则对纯中文零产出)
    entities = extractor.extract_entities("使用《知识图谱》建模")
    assert any(e.name == "知识图谱" for e in entities)


def test_extractor_relation_uses(extractor):
    rels = extractor.extract_relations("Python uses FastAPI")
    assert rels and rels[0].relation_type == "uses"


def test_extractor_empty_text_and_dedup(extractor):
    assert extractor.extract_entities("") == []
    assert extractor.extract_relations("") == []
    # same quoted term twice → deduplicated to one entity
    ents = extractor.extract_entities('x "abc" y "abc"')
    assert sum(1 for e in ents if e.name == "abc") == 1


# ── entity CRUD ──────────────────────────────────────────────────────

def test_add_entity_get_roundtrip(kg):
    eid = kg.add_entity(Entity(name="Alice", entity_type="person", properties={"k": "v"}))
    got = kg.get_entity(eid)
    assert got["name"] == "Alice"
    assert got["entity_type"] == "person"
    assert got["properties"] == {"k": "v"}


def test_get_entity_missing_returns_none(kg):
    assert kg.get_entity("nope") is None


def test_find_entity_by_name_case_insensitive(kg):
    kg.add_entity(Entity(name="Alice"))
    assert kg.find_entity_by_name("ALICE")["name"] == "Alice"
    assert kg.find_entity_by_name("missing") is None


# ── same-name merge (duplicate boundary) ─────────────────────────────

def test_add_entity_same_name_merges(kg):
    e1 = kg.add_entity(Entity(name="Node", properties={"a": 1}, source_memory_ids=["m1"]))
    e2 = kg.add_entity(Entity(name="node", properties={"b": 2}, source_memory_ids=["m2"]))
    assert e1 == e2  # case-insensitive merge → same id, no second row
    got = kg.get_entity(e1)
    assert got["properties"] == {"a": 1, "b": 2}
    assert got["source_memory_ids"] == ["m1", "m2"]


def test_delete_entity_cascades_relations(kg):
    a = kg.add_entity(Entity(name="A"))
    b = kg.add_entity(Entity(name="B"))
    kg.add_relation(Relation(source_entity_id="A", target_entity_id="B", relation_type="uses"))
    assert len(kg.get_relations(a, "outgoing")) == 1

    assert kg.delete_entity(a) is True
    assert kg.get_entity(a) is None
    assert kg.get_relations(b, "incoming") == []  # relation cascade-deleted
    assert kg.delete_entity("nope") is False


# ── relation ops ─────────────────────────────────────────────────────

def test_add_relation_auto_creates_placeholder(kg):
    # endpoints unknown by id/name → placeholder entities auto-created
    rid = kg.add_relation(Relation(source_entity_id="Foo", target_entity_id="Bar", relation_type="uses"))
    assert rid
    foo = kg.find_entity_by_name("Foo")
    assert foo is not None and foo["entity_type"] == "auto_detected"
    assert kg.find_entity_by_name("Bar") is not None
    assert len(kg.get_relations(foo["id"], "outgoing")) == 1


def test_get_relations_direction_and_type_filter(kg):
    a = kg.add_entity(Entity(name="A"))
    b = kg.add_entity(Entity(name="B"))
    c = kg.add_entity(Entity(name="C"))
    kg.add_relation(Relation(source_entity_id="A", target_entity_id="B", relation_type="uses"))
    kg.add_relation(Relation(source_entity_id="B", target_entity_id="C", relation_type="uses"))

    assert len(kg.get_relations(b, "outgoing")) == 1   # B → C
    assert len(kg.get_relations(b, "incoming")) == 1   # A → B
    assert len(kg.get_relations(b, "both")) == 2
    assert len(kg.get_relations(b, "outgoing", relation_type="uses")) == 1
    assert len(kg.get_relations(b, "outgoing", relation_type="nope")) == 0


def test_delete_relation_true_then_false(kg):
    a = kg.add_entity(Entity(name="A"))
    kg.add_entity(Entity(name="B"))
    rid = kg.add_relation(Relation(source_entity_id="A", target_entity_id="B", relation_type="uses"))

    assert kg.delete_relation(rid) is True
    assert kg.get_relations(a, "outgoing") == []
    assert kg.delete_relation(rid) is False


def test_expire_relation_hides_from_queries(kg):
    a = kg.add_entity(Entity(name="A"))
    kg.add_entity(Entity(name="B"))
    rid = kg.add_relation(Relation(source_entity_id="A", target_entity_id="B", relation_type="uses"))

    kg.expire_relation(rid)  # sets valid_to; no return value
    assert kg.get_relations(a, "outgoing") == []  # valid_to IS NULL filter


# ── graph queries (recursive CTE) ────────────────────────────────────

def test_shortest_path_found(kg):
    for n in ("A", "B", "C"):
        kg.add_entity(Entity(name=n))
    kg.add_relation(Relation(source_entity_id="A", target_entity_id="B", relation_type="uses"))
    kg.add_relation(Relation(source_entity_id="B", target_entity_id="C", relation_type="uses"))

    path = kg.shortest_path("A", "C")
    assert path is not None and len(path) == 3
    assert [p["name"] for p in path] == ["A", "B", "C"]


def test_shortest_path_none_when_unreachable(kg):
    for n in ("A", "B", "C"):
        kg.add_entity(Entity(name=n))
    kg.add_relation(Relation(source_entity_id="A", target_entity_id="B", relation_type="uses"))
    kg.add_relation(Relation(source_entity_id="B", target_entity_id="C", relation_type="uses"))

    assert kg.shortest_path("C", "A") is None      # directed: reverse unreachable
    assert kg.shortest_path("A", "missing") is None  # target does not exist


def test_expand_neighborhood(kg):
    for n in ("A", "B", "C"):
        kg.add_entity(Entity(name=n))
    kg.add_relation(Relation(source_entity_id="A", target_entity_id="B", relation_type="uses"))
    kg.add_relation(Relation(source_entity_id="B", target_entity_id="C", relation_type="uses"))

    res = kg.expand("A", depth=2)
    assert res["center"]["name"] == "A"
    assert {n["name"] for n in res["neighbours"]} == {"B", "C"}
    assert len(res["edges"]) >= 2


# ── search / properties / ingest / stats / reuse-score ───────────────

def test_search_entities_substring_and_type(kg):
    kg.add_entity(Entity(name="PostgreSQL", entity_type="technical_term"))
    kg.add_entity(Entity(name="Postman", entity_type="named_entity"))

    assert len(kg.search_entities("Post")) == 2
    tech = kg.search_entities("Post", entity_type="technical_term")
    assert len(tech) == 1 and tech[0]["name"] == "PostgreSQL"


def test_update_entity_properties_merge_and_missing(kg):
    eid = kg.add_entity(Entity(name="E", properties={"a": 1}))
    assert kg.update_entity_properties(eid, {"b": 2}) is True
    assert kg.get_entity(eid)["properties"] == {"a": 1, "b": 2}  # merged, not replaced
    assert kg.update_entity_properties("nope", {}) is False


def test_extract_and_ingest_pipeline(kg):
    res = kg.extract_and_ingest("Python uses FastAPI", memory_id="m1")
    assert "entity_ids" in res and "relation_ids" in res
    assert len(res["entity_ids"]) >= 1          # FastAPI extracted into the graph
    assert kg.stats()["entity_count"] >= 1


def test_stats_counts(kg):
    kg.add_entity(Entity(name="A", entity_type="person"))
    kg.add_entity(Entity(name="B", entity_type="tool"))
    kg.add_relation(Relation(source_entity_id="A", target_entity_id="B", relation_type="uses"))

    s = kg.stats()
    assert s["entity_count"] == 2
    assert s["relation_count"] == 1
    assert "person" in s["entity_types"] and "uses" in s["relation_types"]


def test_query_entities_sorted_by_reuse_score(kg):
    e1 = kg.add_entity(Entity(name="E1"))
    e2 = kg.add_entity(Entity(name="E2"))
    kg.add_entity(Entity(name="E3"))  # no reuse_score → excluded
    kg.update_entity_properties(e1, {"reuse_score": 0.5})
    kg.update_entity_properties(e2, {"reuse_score": 0.9})

    res = kg.query_entities_sorted_by_reuse_score()
    assert [r["name"] for r in res] == ["E2", "E1"]  # desc, E3 filtered
