"""Gateway forwarding tests — guard against /v1 prefix drift.

Every business route must forward to ``{ORCHESTRATOR_URL}/v1/<path>`` (orchestrator
engine.py:367-370 mounts all routers under prefix='/v1'), never a naked path.
Tests assert the captured forwarded URL only (the mock body shape may not match
every route's response_model) — failure means a route dropped the /v1 prefix.

See docs/mvp-iteration-roadmap.md Phase 0.
"""
from fastapi.testclient import TestClient

from src.main import app

# Body shape from the mock may not match every route's response_model, so don't
# raise on server-side validation errors — we only care that forwarding happened.
_CLIENT = TestClient(app, raise_server_exceptions=False)


def _urls(seen):
    return [url for _, url in seen]


def test_agents_forwards_with_v1_prefix(captured_urls):
    with _CLIENT as c:
        c.get("/agents")
    assert any("/v1/agents" in u for u in _urls(captured_urls)), _urls(captured_urls)


def test_memories_forwards_with_v1_prefix(captured_urls):
    with _CLIENT as c:
        c.get("/memories")
    assert any("/v1/memories" in u for u in _urls(captured_urls)), _urls(captured_urls)


def test_messages_forwards_with_v1_prefix(captured_urls):
    with _CLIENT as c:
        c.get("/messages")
    assert any("/v1/messages" in u for u in _urls(captured_urls)), _urls(captured_urls)


def test_kg_entities_forwards_with_v1_prefix(captured_urls):
    with _CLIENT as c:
        c.get("/kg/entities")
    assert any("/v1/kg/entities" in u for u in _urls(captured_urls)), _urls(captured_urls)


def test_debug_history_forwards_with_v1_prefix(captured_urls):
    with _CLIENT as c:
        c.get("/debug/history")
    assert any("/v1/debug/history" in u for u in _urls(captured_urls)), _urls(captured_urls)


def test_no_naked_orchestrator_path_leaks(captured_urls):
    """Regression guard: NO forwarded request may land on a naked orchestrator
    path (missing /v1). Drives immediate failure on prefix drift."""
    with _CLIENT as c:
        c.get("/agents")
        c.get("/memories")
        c.get("/messages")
        c.get("/kg/entities")
        c.get("/debug/history")
    urls = _urls(captured_urls)
    assert urls, "no requests captured — mock wiring broken"
    for u in urls:
        assert "/v1/" in u, f"naked orchestrator path leaked (no /v1): {u}"
