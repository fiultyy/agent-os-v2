"""Gateway test configuration.

Patches the module-level ``http_client`` bound in every route module (and
main.py) with a MockTransport-backed client, so tests assert forwarded URLs
without a live orchestrator. Primary purpose: guard against /v1 prefix drift.

See docs/mvp-iteration-roadmap.md Phase 0.
"""
import os
import sys
from pathlib import Path

_GATEWAY_ROOT = Path(__file__).resolve().parents[1]
if str(_GATEWAY_ROOT) not in sys.path:
    sys.path.insert(0, str(_GATEWAY_ROOT))

# Tests mock all HTTP; clear proxy env so the real httpx.AsyncClient created at
# src.config import time doesn't reject a SOCKS proxy.
for _k in ("http_proxy", "https_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
    os.environ.pop(_k, None)

# miniconda3 python3.12 ships a broken sqlite3 (undefined symbol sqlite3_deserialize);
# src/routes/auth.py imports sqlite3 — substitute pysqlite3 before any src import.
try:
    import pysqlite3
    sys.modules["sqlite3"] = pysqlite3
    sys.modules["sqlite3.dbapi2"] = pysqlite3.dbapi2
except ImportError:
    pass

import httpx
import pytest

from src import main as main_mod
from src.routes import agents, chat, debug, execute, kg, memories, messages

_ROUTE_MODULES = (agents, chat, debug, execute, kg, memories, messages)


@pytest.fixture(autouse=True)
def _no_init_keys(monkeypatch):
    """AUTH_ENABLED defaults false → require_auth short-circuits; neutralise RSA
    key generation on startup to avoid filesystem side-effects."""
    monkeypatch.setattr(main_mod, "init_keys", lambda: None)


@pytest.fixture
def captured_urls(monkeypatch):
    """Replace every route module's (and main's) bound http_client with a
    MockTransport client recording forwarded requests. Yields (method, url) list."""
    seen: list[tuple[str, str]] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        seen.append((request.method, url))
        # GET endpoints return lists, POST/DELETE dicts. Tests assert the forwarded
        # URL only, so a body mismatch is tolerable (raise_server_exceptions=False).
        if request.method == "GET":
            return httpx.Response(200, json=[])
        return httpx.Response(200, json={"ok": True})

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(_handler))
    # Patch routes AND main (its shutdown closes http_client) so the real
    # config.http_client is never touched across repeated TestClient lifespans.
    for mod in (*_ROUTE_MODULES, main_mod):
        monkeypatch.setattr(mod, "http_client", mock_client)
    yield seen
