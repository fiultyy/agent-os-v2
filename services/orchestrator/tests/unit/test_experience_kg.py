"""Tests for ExperienceKG."""

import pytest
import tempfile
import os

from src.memory.experience_kg import ExperienceKG


@pytest.fixture
def exp_kg():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_experience_kg.db")
        kg = ExperienceKG(db_path=db_path)
        yield kg


class TestExperienceNode:
    def test_create_and_get(self, exp_kg):
        node_id = exp_kg.create_experience_node(
            content="Use kg_memory_query to explore entity relations",
            skill_domain="tool-usage",
            source_entity_id="entity-123",
            reuse_score=5.0,
        )
        node = exp_kg.get_experience_node(node_id)
        assert node is not None
        assert node["content"] == "Use kg_memory_query to explore entity relations"
        assert node["skill_domain"] == "tool-usage"
        assert node["reuse_score"] == 5.0

    def test_get_top_experience_nodes(self, exp_kg):
        id1 = exp_kg.create_experience_node("Low reuse", "domain-a", reuse_score=1.0)
        id2 = exp_kg.create_experience_node("Medium reuse", "domain-a", reuse_score=5.0)
        id3 = exp_kg.create_experience_node("High reuse", "domain-b", reuse_score=10.0)

        top = exp_kg.get_top_experience_nodes(limit=10)
        assert len(top) == 3
        assert top[0]["reuse_score"] == 10.0
        assert top[1]["reuse_score"] == 5.0
        assert top[2]["reuse_score"] == 1.0

    def test_update_reuse_score(self, exp_kg):
        node_id = exp_kg.create_experience_node("Test", "test", reuse_score=3.0)
        exp_kg.update_reuse_score(node_id, 2.0)
        node = exp_kg.get_experience_node(node_id)
        assert node["reuse_score"] == 5.0


class TestExperienceWings:
    def test_create_and_get_forward_wings(self, exp_kg):
        id1 = exp_kg.create_experience_node("Node A", "domain")
        id2 = exp_kg.create_experience_node("Node B", "domain")
        id3 = exp_kg.create_experience_node("Node C", "domain")

        exp_kg.create_wing_edge(id1, id2, "forward", strength=0.8)
        exp_kg.create_wing_edge(id1, id3, "forward", strength=0.6)

        forward = exp_kg.get_forward_wings(id1)
        assert len(forward) == 2
        assert all(w["wing_type"] == "forward" for w in forward)

    def test_build_butterfly_associations(self, exp_kg):
        id1 = exp_kg.create_experience_node("Center Node", "test", reuse_score=10.0)
        id2 = exp_kg.create_experience_node("Forward Node", "test")
        id3 = exp_kg.create_experience_node("Backward Node", "test")

        exp_kg.create_wing_edge(id1, id2, "forward", strength=0.9)
        exp_kg.create_wing_edge(id3, id1, "backward", strength=0.7)

        result = exp_kg.build_butterfly_associations(id1)
        assert result["center"]["content"] == "Center Node"
        assert len(result["forward"]) == 1
        assert len(result["backward"]) == 1
        assert result["composite_score"] > 0


class TestSkillBundle:
    def test_create_and_get_skill_bundle(self, exp_kg):
        id1 = exp_kg.create_experience_node("Skill part 1", "coding")
        id2 = exp_kg.create_experience_node("Skill part 2", "coding")

        bundle_id = exp_kg.create_skill_bundle(
            name="Coding Helper Skill",
            node_ids=[id1, id2],
            domain="coding",
            description="A skill for coding assistance",
        )

        bundle = exp_kg.get_skill_bundle(bundle_id)
        assert bundle is not None
        assert bundle["name"] == "Coding Helper Skill"
        assert len(bundle["node_ids"]) == 2

    def test_get_skill_bundles_by_domain(self, exp_kg):
        id1 = exp_kg.create_experience_node("A", "python")
        id2 = exp_kg.create_experience_node("B", "javascript")

        exp_kg.create_skill_bundle("Python Skill", [id1], "python")
        exp_kg.create_skill_bundle("JS Skill", [id2], "javascript")

        python_skills = exp_kg.get_skill_bundles(domain="python")
        assert len(python_skills) == 1
        assert python_skills[0]["name"] == "Python Skill"


class TestExperienceKGStats:
    def test_stats(self, exp_kg):
        exp_kg.create_experience_node("Node 1", "domain-a")
        exp_kg.create_experience_node("Node 2", "domain-a")
        exp_kg.create_experience_node("Node 3", "domain-b")

        stats = exp_kg.stats()
        assert stats["node_count"] == 3
        assert stats["wing_count"] == 0
        assert stats["bundle_count"] == 0
        assert "domain-a" in stats["domains"]
        assert stats["domains"]["domain-a"] == 2
