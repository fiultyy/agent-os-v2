"""Tests for P3 TimeBasedStatePruner — deterministic ACTIVE→STALE→ARCHIVED."""

import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

try:
    import pysqlite3

    sys.modules["sqlite3"] = pysqlite3
    sys.modules["sqlite3.dbapi2"] = pysqlite3.dbapi2
except ImportError:
    pass

import pytest

from src.memory.service import MemoryService
from src.memory.store import InMemoryStore
from src.memory.state_pruner import TimeBasedStatePruner, StatePrunerConfig
from src.memory.types import MemoryItem, MemoryOrigin, MemoryState


def _svc() -> MemoryService:
    return MemoryService(store=InMemoryStore())


def _days_ago(days: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


async def _seed(svc: MemoryService, **kw: object) -> str:
    """Store a MemoryItem directly (bypasses CrudOperations.store + update's
    touch(), so accessed_at is preserved for age-based tests)."""
    item = MemoryItem(**kw)  # type: ignore[arg-type]
    await svc._store.store(item)
    return item.id


class TestStatePruner:
    @pytest.mark.asyncio
    async def test_recent_active_unchanged(self) -> None:
        svc = _svc()
        pruner = TimeBasedStatePruner(svc)
        ref = await svc.store(content="recent", agent_id="a", origin=MemoryOrigin.AGENT, importance=0.8)
        r = await pruner.prune(agent_id="a")
        assert r.to_stale == 0 and r.to_archived == 0
        assert (await svc.get(ref.id)).state == MemoryState.ACTIVE

    @pytest.mark.asyncio
    async def test_stale_after_stale_days(self) -> None:
        svc = _svc()
        pruner = TimeBasedStatePruner(svc, StatePrunerConfig(stale_days=30, archive_days=90))
        item_id = await _seed(
            svc, content="cold", agent_id="a",
            origin=MemoryOrigin.AGENT, importance=0.8, accessed_at=_days_ago(40),
        )
        r = await pruner.prune(agent_id="a")
        assert r.to_stale == 1 and r.to_archived == 0
        it = await svc.get(item_id)
        assert it.state == MemoryState.STALE
        assert it.archived is False

    @pytest.mark.asyncio
    async def test_archived_after_archive_days(self) -> None:
        svc = _svc()
        pruner = TimeBasedStatePruner(svc)
        item_id = await _seed(
            svc, content="very cold", agent_id="a",
            origin=MemoryOrigin.AGENT, importance=0.8, accessed_at=_days_ago(100),
        )
        r = await pruner.prune(agent_id="a")
        assert r.to_archived == 1
        it = await svc.get(item_id)
        assert it.state == MemoryState.ARCHIVED
        assert it.archived is True  # legacy flag synced

    @pytest.mark.asyncio
    async def test_low_importance_archived_even_when_recent(self) -> None:
        svc = _svc()
        pruner = TimeBasedStatePruner(svc, StatePrunerConfig(archive_importance=0.1))
        ref = await svc.store(content="low value", agent_id="a", origin=MemoryOrigin.AGENT, importance=0.05)
        r = await pruner.prune(agent_id="a")
        assert r.to_archived == 1

    @pytest.mark.asyncio
    async def test_foreground_protected_not_scanned(self) -> None:
        svc = _svc()
        pruner = TimeBasedStatePruner(svc)
        await _seed(
            svc, content="user memo", agent_id="a",
            origin=MemoryOrigin.FOREGROUND, accessed_at=_days_ago(200),
        )
        r = await pruner.prune(agent_id="a")
        assert r.scanned == 0  # FOREGROUND filtered out

    @pytest.mark.asyncio
    async def test_already_archived_skipped(self) -> None:
        svc = _svc()
        pruner = TimeBasedStatePruner(svc)
        await _seed(
            svc, content="done", agent_id="a", origin=MemoryOrigin.AGENT,
            importance=0.8, state=MemoryState.ARCHIVED, archived=True,
            accessed_at=_days_ago(200),
        )
        r = await pruner.prune(agent_id="a")
        assert r.scanned == 0  # scan is ACTIVE-only
