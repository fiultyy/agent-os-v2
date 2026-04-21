"""Tests for InterceptLayer."""
import pytest
from src.control.intercept_layer import InterceptLayer, InterceptResult


@pytest.fixture
def layer():
    return InterceptLayer()


def test_add_rule(layer):
    layer.add_rule("forbidden", "block", "contains forbidden content")
    assert len(layer._rules) == 1
    assert layer._rules[0]["pattern"] == "forbidden"
    assert layer._rules[0]["action"] == "block"


@pytest.mark.asyncio
async def test_intercept_request_allow_no_match(layer):
    layer.add_rule("forbidden", "block", "blocked")
    result = await layer.intercept_request([{"role": "user", "content": "hello world"}])
    assert result.action == "allow"
    assert result.modified is None


@pytest.mark.asyncio
async def test_intercept_request_block_match(layer):
    layer.add_rule("forbidden", "block", "blocked")
    result = await layer.intercept_request([{"role": "user", "content": "this is forbidden text"}])
    assert result.action == "block"
    assert result.reason == "blocked"


@pytest.mark.asyncio
async def test_intercept_request_modify_match(layer):
    layer.add_rule("sensitive", "modify", "redact sensitive")
    result = await layer.intercept_request([{"role": "user", "content": "user sensitive data here"}])
    assert result.action == "modify"
    assert result.modified is not None


@pytest.mark.asyncio
async def test_intercept_response_allow_no_match(layer):
    layer.add_rule("forbidden", "block", "blocked")
    result = await layer.intercept_response({"content": "normal response"})
    assert result.action == "allow"


@pytest.mark.asyncio
async def test_intercept_response_block_match(layer):
    layer.add_rule("danger", "block", "dangerous content")
    result = await layer.intercept_response({"content": "this is a danger response"})
    assert result.action == "block"


@pytest.mark.asyncio
async def test_get_log(layer):
    layer.add_rule("forbidden", "block", "blocked")
    await layer.intercept_request([{"role": "user", "content": "forbidden text"}])
    log = layer.get_log()
    assert len(log) == 1
    assert log[0]["type"] == "request"
    assert log[0]["action"] == "block"


def test_clear_log(layer):
    layer.clear_log()
    assert len(layer.get_log()) == 0
