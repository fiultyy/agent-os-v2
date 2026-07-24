"""ADR-3 单测:create_agent 工具注册 + 行为(queen 写文件能力)。

verify(worker-A.md T2 acceptance,逐字):
- create_agent 注册到 tool_executor(``_tool_registry.get('create_agent')`` 命中)
- register 名 **无 v2_ 前缀**(RK11)— 经 ToolBridge 后模型可见 ``v2_create_agent``
- 传入合法配置 → agents.yaml append + workspace 文件创建 + agents.yaml 仍能
  ``AgentsConfig.model_validate`` 通过
- 同名 id → 拒(不覆盖现有,灾难底线)
- 原子写(临时文件 + os.replace,写错不破坏现有 agents.yaml)

隔离:所有 handler 行为测用 ``config_path=tmp_path/agents.yaml`` 注入,
**不碰真实 repo 根 agents.yaml**。workspace 也落在 tmp_path 下。
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest
import yaml

import src.engine as engine_mod
from src.agent.agent_spec import AgentsConfig
from src.tools.agent_creator import create_agent

_tool_registry = engine_mod._tool_registry
_tool_executor = engine_mod._state.tool_executor


# ─────────────────────────────────────────────────────────────────────
# T2 verify(1):register 名 'create_agent' 命中(无 v2_ 前缀)
# ─────────────────────────────────────────────────────────────────────
def test_create_agent_registered_unprefixed():
    """register 名 = 'create_agent'(无 v2_ 前缀)— ToolBridge 自动加成 v2_create_agent。"""
    tool = _tool_registry.get("create_agent")
    assert tool is not None, "create_agent not registered (engine._n_creator block missing)"
    params = tool["parameters"]
    assert params["required"] == ["id"]
    # pattern 对齐 AgentSpec._ID_PATTERN
    assert "^[a-z0-9][a-z0-9_-]{0,63}$" in params["properties"]["id"]["pattern"]


def test_create_agent_registered_at_composite_layer():
    """layer = ToolLayer.COMPOSITE(与 workflow_run / a2a_call 同层,创建 agent 多步文件操作)。"""
    from src.tools.catalog import ToolLayer
    entries = _tool_registry.get_catalog().entries
    entry = entries.get("create_agent")
    assert entry is not None, "create_agent not in catalog"
    assert entry.layer == ToolLayer.COMPOSITE


def test_v2_prefixed_name_not_registered():
    """RK11 反向断言:register 名必须 **不是** 'v2_create_agent'(否则双前缀)。"""
    assert _tool_registry.get("v2_create_agent") is None, (
        "register 名误带 v2_ 前缀 — ToolBridge 会再加成 v2_v2_create_agent (RK11 violation)"
    )
    assert _tool_registry.get("v2_v2_create_agent") is None


# ─────────────────────────────────────────────────────────────────────
# T2 verify(2):经 ToolBridgeCapability 后模型可见 'v2_create_agent'(无双前缀)
# ─────────────────────────────────────────────────────────────────────
def test_model_visible_name_is_v2_create_agent_no_double_prefix():
    """RK11:ToolBridge .prefixed('v2') 自动加成 v2_create_agent。"""
    from pydantic_ai._run_context import RunContext
    from pydantic_ai.models.test import TestModel
    from pydantic_ai.usage import RunUsage

    from src.harness.capabilities.tool_bridge_capability import ToolBridgeCapability

    ctx = RunContext(deps=None, model=TestModel(), usage=RunUsage())
    cap = ToolBridgeCapability(tool_executor=_tool_executor)
    ts = cap.get_toolset()
    names = asyncio.run(ts.get_tools(ctx)).keys()

    assert "v2_create_agent" in names, (
        f"model-visible name missing 'v2_create_agent'; got: {sorted(names)}"
    )
    assert "v2_v2_create_agent" not in names, "RK11 FAIL: double v2_ prefix"
    assert "create_agent" not in names, "bare name leaked (must be under v2_ namespace)"


# ─────────────────────────────────────────────────────────────────────
# T2 verify(3):合法配置 → 写成功 + schema 校验通过 + workspace 文件创建
# ─────────────────────────────────────────────────────────────────────
def _seed_agents_yaml(tmp_path: Path) -> Path:
    """在 tmp_path 种一份最小合法 agents.yaml(defaults + 1 default agent)。"""
    config = tmp_path / "agents.yaml"
    config.write_text(
        """
defaults:
  model: glm-4.7
  workspace_base: null
  skills: []
agents:
  - id: help
    default: true
    name: AO2 新手向导
    model: glm-4.7
    workspace: /tmp/_test_create_agent_seed/workspace
    skills: [ao2-help]
    instructions: |
      seed agent
""".strip(),
        encoding="utf-8",
    )
    return config


def test_create_agent_valid_config_writes_and_schema_passes(tmp_path):
    """合法配置:create_agent 写成功 + agents.yaml 仍能 AgentsConfig.model_validate。"""
    config = _seed_agents_yaml(tmp_path)
    workspace = tmp_path / "ws-researcher"

    result = create_agent(
        id="researcher",
        name="研究员",
        model="glm-4.7",
        workspace=str(workspace),
        skills=["agent-creator"],
        instructions="你是研究员。",
        soul_md="# 研究员\n\n> 探索者",
        agents_md="# AGENTS.md\n\n## Agent Behavior\n...",
        config_path=str(config),
    )

    # 返回结构
    assert result["ok"] is True
    assert result["agent_id"] == "researcher"
    assert result["workspace"] == str(workspace)
    assert "重启 orche 生效" in result["note"]

    # workspace 文件落盘
    assert workspace.is_dir()
    assert (workspace / "SOUL.md").read_text(encoding="utf-8").startswith("# 研究员")
    assert (workspace / "AGENTS.md").read_text(encoding="utf-8").startswith("# AGENTS.md")

    # agents.yaml append 了新条目(原 help 仍在)
    raw = yaml.safe_load(config.read_text(encoding="utf-8"))
    ids = [a["id"] for a in raw["agents"]]
    assert ids == ["help", "researcher"], f"expected append preserving existing; got {ids}"

    # 整份 schema 仍过(AgentsConfig.model_validate 不抛)
    cfg = AgentsConfig.model_validate(raw)
    assert {a.id for a in cfg.agents} == {"help", "researcher"}
    assert cfg.agents[0].default is True  # 原 default 保留
    assert cfg.agents[1].default is False  # 新条目不抢 default


def test_create_agent_minimal_config_only_id(tmp_path):
    """只传 id(最小合法配置):defaults.model 回退 + workspace 兜底 stateDir。

    不传 workspace → _resolve_workspace 走 stateDir 派生兜底,不崩。
    """
    config = _seed_agents_yaml(tmp_path)
    # monkeypatch AO2_STATE_DIR 指向 tmp,避免污染真实 ~/.agent-os
    monkey_state = tmp_path / "state"
    monkey_state.mkdir()
    old = os.environ.get("AO2_STATE_DIR")
    os.environ["AO2_STATE_DIR"] = str(monkey_state)
    try:
        result = create_agent(id="bare", config_path=str(config))
    finally:
        if old is None:
            os.environ.pop("AO2_STATE_DIR", None)
        else:
            os.environ["AO2_STATE_DIR"] = old

    assert result["ok"] is True
    assert result["agent_id"] == "bare"
    # workspace 落在 stateDir 派生路径
    assert str(monkey_state / "agents" / "bare" / "workspace") == result["workspace"]


# ─────────────────────────────────────────────────────────────────────
# T2 verify(4):同名 id → 拒(灾难底线:不覆盖现有 agent)
# ─────────────────────────────────────────────────────────────────────
def test_create_agent_rejects_duplicate_id(tmp_path):
    """同名 id 拒(灾难底线):不覆盖现有 agent。

    agents.yaml 已有 'help',再 create 'help' → raise ValueError。
    """
    config = _seed_agents_yaml(tmp_path)
    raw_before = config.read_text(encoding="utf-8")

    with pytest.raises(ValueError, match="already exists"):
        create_agent(
            id="help",
            name="试图覆盖",
            workspace=str(tmp_path / "ws-evil"),
            config_path=str(config),
        )

    # agents.yaml 一字未动(灾难底线:拒绝时不破坏现有配置)
    assert config.read_text(encoding="utf-8") == raw_before


def test_create_agent_rejects_invalid_id(tmp_path):
    """非法 id(大写/非法字符)→ raise,不静默 normalize。

    虽然 normalize_agent_id 会把 'Bad_Id!' 收敛成 'bad-id',但工具拒绝
    让调用方明确修 id(避免静默改名导致配置不一致)。
    """
    config = _seed_agents_yaml(tmp_path)
    with pytest.raises(ValueError, match="invalid agent id"):
        create_agent(id="Bad_Id!", config_path=str(config))


# ─────────────────────────────────────────────────────────────────────
# T2 verify(5):原子写 — 写错不破坏现有 agents.yaml
# ─────────────────────────────────────────────────────────────────────
def test_create_agent_atomic_write_preserves_existing_on_failure(tmp_path, monkeypatch):
    """原子写:写过程失败时现有 agents.yaml 完好(临时文件 + os.replace 语义)。

    monkeypatch os.replace 让其抛错(模拟 rename 失败),验证:
    ① 现有 agents.yaml 内容不变
    ② 临时文件被清理(tmp_path 下不留 .agents.yaml.*.tmp)
    """
    config = _seed_agents_yaml(tmp_path)
    raw_before = config.read_text(encoding="utf-8")

    import src.tools.agent_creator as ac_mod

    real_replace = os.replace

    def _boom(*args, **kwargs):
        raise OSError("simulated rename failure")

    # 只 patch agents.yaml 的 replace(workspace 文件 replace 放行)
    call_count = {"n": 0}
    orig_yaml_write = ac_mod._atomic_write_yaml

    def _patched_yaml_write(target, data):
        call_count["n"] += 1
        # 临时替换 os.replace 让其炸
        monkeypatch.setattr(ac_mod.os, "replace", _boom)
        try:
            orig_yaml_write(target, data)
        finally:
            monkeypatch.setattr(ac_mod.os, "replace", real_replace)

    monkeypatch.setattr(ac_mod, "_atomic_write_yaml", _patched_yaml_write)

    with pytest.raises(OSError, match="simulated rename failure"):
        create_agent(
            id="doomed",
            workspace=str(tmp_path / "ws-doomed"),
            config_path=str(config),
        )

    # 现有 agents.yaml 完好(原子写核心保证)
    assert config.read_text(encoding="utf-8") == raw_before
    # 临时文件清理(不留 .agents.yaml.*.tmp)
    tmps = list(tmp_path.glob(".agents.yaml.*.tmp"))
    assert tmps == [], f"temp file leaked: {tmps}"


def test_create_agent_optional_skill_creates_skill_file(tmp_path):
    """传 skill_name + skill_md → 创建 services/skills/<name>/SKILL.md。

    skill 目录相对 repo root 推断(从 agents.yaml 路径上溯 .git)。
    本测在 tmp_path 下造假 repo root(.git 目录),隔离真实 repo。
    """
    # 造假 repo root:tmp_path/.git + tmp_path/agents.yaml + tmp_path/services/skills
    fake_repo = tmp_path / "fakerepo"
    fake_repo.mkdir()
    (fake_repo / ".git").mkdir()  # _repo_root 上溯命中
    (fake_repo / "services" / "skills").mkdir(parents=True)
    config = fake_repo / "agents.yaml"
    config.write_text(
        "defaults:\n  model: glm-4.7\nagents:\n  - id: seed\n    default: true\n",
        encoding="utf-8",
    )

    result = create_agent(
        id="researcher",
        workspace=str(fake_repo / "ws"),
        skill_name="researcher-tool",
        skill_md="---\nname: researcher-tool\ndescription: test\n---\n# body",
        config_path=str(config),
    )

    skill_file = fake_repo / "services" / "skills" / "researcher-tool" / "SKILL.md"
    assert skill_file.is_file(), f"skill file not created at {skill_file}"
    assert "name: researcher-tool" in skill_file.read_text(encoding="utf-8")
    assert str(skill_file) in result["files_written"]


def test_create_agent_skill_name_without_content_rejected(tmp_path):
    """skill_name 提供但 skill_md 缺 → ValueError(不让建空 skill)。"""
    config = _seed_agents_yaml(tmp_path)
    with pytest.raises(ValueError, match="skill_name provided without skill_md"):
        create_agent(
            id="researcher",
            workspace=str(tmp_path / "ws"),
            skill_name="incomplete",
            skill_md=None,
            config_path=str(config),
        )
