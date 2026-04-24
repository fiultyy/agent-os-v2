"""E2E tests for D-31 Endless Canvas — Branch CRUD + WebSocket + Persistence."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import time
import uuid
from pathlib import Path

import pytest
import requests

BACKEND_URL = "http://127.0.0.1:18792"
WS_URL = "ws://127.0.0.1:18792/ws/canvas"
DB_PATH = Path(__file__).parent.parent.parent.parent / "data" / "canvas_events.db"


class BackendProcess:
    """Context manager: start/stop the backend process with auth disabled."""

    def __init__(self, port: int = 18792):
        self.port = port
        self.proc: subprocess.Popen | None = None

    def __enter__(self) -> "BackendProcess":
        env = os.environ.copy()
        # Disable token auth and allow all origins for E2E tests
        env.pop("CANVAS_API_TOKEN", None)
        env["CANVAS_ALLOWED_ORIGINS"] = "*"
        self.proc = subprocess.Popen(
            [".venv/bin/python3", "start.py"],
            cwd=str(Path(__file__).parent.parent.parent),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        # Wait for health endpoint
        for _ in range(20):
            try:
                r = requests.get(f"http://127.0.0.1:{self.port}/health", timeout=1)
                if r.status_code == 200:
                    return self
            except requests.exceptions.ConnectionError:
                pass
            time.sleep(0.5)
        raise RuntimeError("Backend failed to start")

    def __exit__(self, *args):
        if self.proc:
            self.proc.terminate()
            self.proc.wait(timeout=5)


class TestL1BackendHealth:
    """L1: Backend startup and health."""

    def test_health_ok(self):
        with BackendProcess() as backend:
            r = requests.get(f"{BACKEND_URL}/health", timeout=5)
            assert r.status_code == 200
            assert r.json()["status"] == "ok"


class TestL2BranchCRUD:
    """L2: Branch Create / Merge / Prune REST API."""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.backend = BackendProcess().__enter__()
        yield
        self.backend.__exit__(None, None, None)

    def _session(self) -> str:
        return f"e2e-{uuid.uuid4().hex[:8]}"

    def test_create_branch(self):
        sid = self._session()
        r = requests.post(
            f"{BACKEND_URL}/api/canvas/branch/create",
            json={"session_id": sid, "parent_branch_id": "main", "fork_tick_id": ""},
            timeout=5,
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert "branch_id" in data
        assert data["status"] == "active"
        assert data["session_id"] == sid

    def test_create_and_merge(self):
        sid = self._session()
        r = requests.post(
            f"{BACKEND_URL}/api/canvas/branch/create",
            json={"session_id": sid, "parent_branch_id": "main", "fork_tick_id": ""},
            timeout=5,
        )
        branch_id = r.json()["branch_id"]
        r = requests.post(
            f"{BACKEND_URL}/api/canvas/branch/merge",
            json={"branch_id": branch_id, "session_id": sid, "target_branch_id": "main"},
            timeout=5,
        )
        assert r.status_code == 200
        assert r.json()["status"] == "merged"
        assert r.json()["merged_at"] is not None

    def test_create_and_prune(self):
        sid = self._session()
        r = requests.post(
            f"{BACKEND_URL}/api/canvas/branch/create",
            json={"session_id": sid, "parent_branch_id": "main", "fork_tick_id": ""},
            timeout=5,
        )
        branch_id = r.json()["branch_id"]
        r = requests.post(
            f"{BACKEND_URL}/api/canvas/branch/prune",
            json={"branch_id": branch_id, "session_id": sid},
            timeout=5,
        )
        assert r.status_code == 200
        assert r.json()["status"] == "pruned"

    def test_merge_nonexistent_returns_404(self):
        r = requests.post(
            f"{BACKEND_URL}/api/canvas/branch/merge",
            json={"branch_id": "does-not-exist", "session_id": self._session(), "target_branch_id": "main"},
            timeout=5,
        )
        assert r.status_code == 404

    def test_prune_main_returns_404(self):
        # "main" branch is not in BranchStore (created on-demand), so 404
        r = requests.post(
            f"{BACKEND_URL}/api/canvas/branch/prune",
            json={"branch_id": "main", "session_id": self._session()},
            timeout=5,
        )
        assert r.status_code == 404


class TestL3KGAPI:
    """L3: Knowledge Graph API."""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.backend = BackendProcess().__enter__()
        yield
        self.backend.__exit__(None, None, None)

    def test_kg_stats(self):
        r = requests.get(f"{BACKEND_URL}/v1/kg/stats", timeout=5)
        assert r.status_code == 200
        data = r.json()
        assert "entity_count" in data
        assert "relation_count" in data


class TestL4WebSocket:
    """L4: WebSocket handshake, subscribe, ping."""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.backend = BackendProcess().__enter__()
        yield
        self.backend.__exit__(None, None, None)

    def _ws(self, session_id: str):
        try:
            import websockets
        except ImportError:
            pytest.skip("websockets not installed")
        return websockets.connect(
            f"{WS_URL}?session_id={session_id}&tab_id=test-tab",
            origin="http://127.0.0.1:18792",
        )

    @pytest.mark.asyncio
    async def test_ws_connect_and_subscribe(self):
        sid = f"ws-{uuid.uuid4().hex[:8]}"
        async with self._ws(sid) as ws:
            await ws.send(json.dumps({"cmd": "subscribe", "session_id": sid}))
            resp = await asyncio.wait_for(ws.recv(), timeout=5)
            data = json.loads(resp)
            assert data["cmd"] == "subscribed"
            assert data["session_id"] == sid

    @pytest.mark.asyncio
    async def test_ws_ping_pong(self):
        sid = f"ws-{uuid.uuid4().hex[:8]}"
        async with self._ws(sid) as ws:
            await ws.send(json.dumps({"cmd": "ping"}))
            resp = await asyncio.wait_for(ws.recv(), timeout=5)
            data = json.loads(resp)
            assert data["cmd"] == "pong"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
