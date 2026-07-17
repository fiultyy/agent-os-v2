"""L2: routes.list_claw_agents — regression: read agents.list, NOT feishu accounts.

Guards the fix (memory: orch-flow-feishu-pollution): the old impl read
``channels.feishu.accounts`` (飞书 bot 凭证 {appId, appSecret}) and presented
their keys as agents → default/origin-cc leaked into the picker → flow
sessions.send hit dead session_keys. Now reads ``agents.list`` (real agent
definitions). These tests pin the new source + fallbacks.
"""

import json
from pathlib import Path

import pytest

from src.harness.routes import list_claw_agents


def _write_cfg(home: Path, cfg: dict) -> None:
    d = home / ".openclaw"
    d.mkdir(parents=True, exist_ok=True)
    (d / "openclaw.json").write_text(json.dumps(cfg))


@pytest.mark.asyncio
async def test_reads_agents_list_ignores_feishu_accounts(monkeypatch, tmp_path):
    """Real agents from agents.list; feishu account keys must NOT appear."""
    cfg = {
        "agents": {"list": [
            {"id": "main", "model": "glm-5.2"},
            {"id": "claw-02", "model": "glm-5.2"},
            {"id": "project-expert-00"},
        ]},
        "channels": {"feishu": {"accounts": {
            "default": {"appId": "x", "appSecret": "y"},
            "origin-cc": {"appId": "a", "appSecret": "b"},
        }}},
    }
    _write_cfg(tmp_path, cfg)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    res = await list_claw_agents()
    assert res["agents"] == ["main", "claw-02", "project-expert-00"]
    assert res["default"] == "main"
    # regression: feishu bot credential keys never surface as agents
    assert "default" not in res["agents"]
    assert "origin-cc" not in res["agents"]


@pytest.mark.asyncio
async def test_default_falls_back_to_first_when_no_main(monkeypatch, tmp_path):
    _write_cfg(tmp_path, {"agents": {"list": [{"id": "claw-02"}]}})
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    res = await list_claw_agents()
    assert res["agents"] == ["claw-02"]
    assert res["default"] == "claw-02"


@pytest.mark.asyncio
async def test_agent_id_falls_back_to_name(monkeypatch, tmp_path):
    _write_cfg(tmp_path, {"agents": {"list": [{"name": "named-only"}]}})
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    res = await list_claw_agents()
    assert res["agents"] == ["named-only"]


@pytest.mark.asyncio
async def test_falls_back_to_main_when_agents_list_empty(monkeypatch, tmp_path):
    """agents.list missing/empty (even with feishu accounts present) → [main]."""
    _write_cfg(tmp_path, {"channels": {"feishu": {"accounts": {"default": {}}}}})
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    res = await list_claw_agents()
    assert res["agents"] == ["main"]
    assert res["default"] == "main"


@pytest.mark.asyncio
async def test_falls_back_to_main_when_cfg_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)   # no .openclaw dir
    res = await list_claw_agents()
    assert res["agents"] == ["main"]
    assert res["default"] == "main"
