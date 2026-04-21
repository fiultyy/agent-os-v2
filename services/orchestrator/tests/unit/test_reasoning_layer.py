"""Tests for ReasoningLayer."""
import pytest
from datetime import datetime, timezone
from src.control.reasoning_layer import ReasoningLayer, ReasoningStep


@pytest.fixture
def layer():
    return ReasoningLayer()


@pytest.fixture
def session_id():
    return "test-session-1"


def make_step(step_id: str, parent_id: str | None, content: str = "test reasoning") -> ReasoningStep:
    return ReasoningStep(
        id=step_id,
        parent_id=parent_id,
        content=content,
        model="test-model",
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


@pytest.mark.asyncio
async def test_capture_step(layer, session_id):
    step = make_step("1", None, "first thought")
    await layer.capture_step(session_id, step)
    chain = layer.get_chain(session_id)
    assert len(chain) == 1
    assert chain[0].id == "1"


@pytest.mark.asyncio
async def test_capture_multiple_steps(layer, session_id):
    step1 = make_step("1", None, "root thought")
    step2 = make_step("2", "1", "child thought")
    step3 = make_step("3", "1", "another child")
    await layer.capture_step(session_id, step1)
    await layer.capture_step(session_id, step2)
    await layer.capture_step(session_id, step3)
    chain = layer.get_chain(session_id)
    assert len(chain) == 3


def test_get_chain_empty(layer, session_id):
    assert layer.get_chain(session_id) == []


def test_build_tree_single_root(layer, session_id):
    step = make_step("1", None, "root")
    layer._chains[session_id] = [step]
    tree = layer.build_tree(session_id)
    assert tree["session_id"] == session_id
    assert len(tree["roots"]) == 1
    assert tree["roots"][0]["id"] == "1"
    assert tree["roots"][0]["children"] == []


def test_build_tree_with_children(layer, session_id):
    step1 = make_step("1", None, "root")
    step2 = make_step("2", "1", "child1")
    step3 = make_step("3", "1", "child2")
    step4 = make_step("4", "2", "grandchild")
    layer._chains[session_id] = [step1, step2, step3, step4]
    tree = layer.build_tree(session_id)
    assert len(tree["roots"]) == 1
    assert len(tree["roots"][0]["children"]) == 2
    assert tree["roots"][0]["children"][0]["id"] == "2"
    assert tree["roots"][0]["children"][0]["children"][0]["id"] == "4"


def test_get_path_summary(layer, session_id):
    step1 = make_step("1", None, "root reasoning step one")
    step2 = make_step("2", "1", "child step two")
    step3 = make_step("3", "2", "grandchild step three")
    layer._chains[session_id] = [step1, step2, step3]
    summary = layer.get_path_summary(session_id)
    assert "Step 1" in summary
    assert "Step 2" in summary
    assert "Step 3" in summary


def test_get_path_summary_empty(layer):
    result = layer.get_path_summary("nonexistent-session")
    assert result == ""


def test_clear(layer, session_id):
    step = make_step("1", None, "root")
    layer._chains[session_id] = [step]
    layer.clear(session_id)
    assert layer.get_chain(session_id) == []
    assert session_id not in layer._chains
