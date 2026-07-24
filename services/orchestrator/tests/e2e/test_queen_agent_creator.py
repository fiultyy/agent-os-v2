"""B.T3 集成测试:queen agent 端到端 capability assembly(ADR-1/ADR-2/ADR-3)。

verify(worker-B.md T3 acceptance):
- queen spec(registry 读 agents.yaml)→ ``assemble_capabilities`` → caps 含
  ``SkillCapability(id='agent-creator', defer_loading=True)``
- queen ``tools.allow=['create_agent']`` → ``ToolBridgeCapability`` 透传 →
  ``get_toolset()`` 经 ``.prefixed('v2')`` 暴露 ``v2_create_agent``(allow 白名单
  在 prefixed 前 filter,注册名无 v2_ 前缀 RK11)
- queen 能 load agent-creator skill(skill body 可读)
- (调用链)ToolBridge dispatch → tool_executor → create_agent handler 真存在且可调
  (用 tmp 隔离 agents.yaml,不碰真实 repo 配置)

**不真调 GLM**:本测只验 capability assembly + tool 桥接结构,不打 ``agent.run``
(那需 e2e marker + 真 token,本文件零 marker 零网络,归 unit 套件可跑)。
``assemble_capabilities`` 共享自 ``_build_native_session`` + A2A transport,是
queen 上线后真跑的同一装配路径(ADR-4 peer stack),结构断言等价于 queen 通电。
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

# 触发 engine 装配(_state.agent_registry / tool_executor / profile_registry)。
import src.engine as engine_mod  # noqa: F401
from src.agent.agent_spec import AgentsConfig
from src.harness.capabilities import (
    ToolBridgeCapability,
    make_skill_capabilities,
)
from src.harness.capabilities.skill_capability import SkillCapability
from src.skills.skill_loader import SkillLoader
from src.tools.agent_creator import create_agent

_state = engine_mod._state


# ─────────────────────────────────────────────────────────────────────
# helper:mock emitter(assemble_capabilities 只透传,不连 observe)
# ─────────────────────────────────────────────────────────────────────
def _mock_emitter(session_id: str = "queen-test"):
    """ObserveEmitter 替身:assemble_capabilities 只把它塞进 caps 字段,不调 connect。"""
    em = MagicMock()
    em.session_id = session_id
    em.harness_id = "queen-test-harness"
    return em


def _resolve_queen_spec():
    """从真 registry 取 queen spec(agents.yaml 已含 queen 条目)。"""
    reg = _state.agent_registry
    assert reg is not None, "engine 装配失败:agent_registry 未初始化"
    spec = reg.get("queen")
    assert spec is not None, (
        "queen agent 不在 registry —— agents.yaml 未加 queen 条目,或 AO2_REPO_ROOT "
        "指向旧配置(检查 env / cwd)"
    )
    return spec


# ═════════════════════════════════════════════════════════════════════
# T3 verify(1):assemble_capabilities → caps 含 agent-creator SkillCapability
# ═════════════════════════════════════════════════════════════════════
def test_queen_assemble_has_agent_creator_skill():
    """queen spec.skills=['agent-creator'] → caps 含 SkillCapability(id='agent-creator')。"""
    from src.harness.routes import assemble_capabilities

    spec = _resolve_queen_spec()
    assert spec.skills == ["agent-creator"], f"queen skills: {spec.skills}"

    caps, model_name = assemble_capabilities(
        spec,
        agent_id_for_scope="queen",
        session_id="queen-test",
        harness_id="queen-test-harness",
        emitter=_mock_emitter(),
        session_key="agent-os-v2:queen-test",
        cwd_scope=[],  # queen 单 workspace cwd,cwd_scope 空(退化)
    )

    skill_caps = [c for c in caps if isinstance(c, SkillCapability)]
    ids = {c.id for c in skill_caps}
    assert "agent-creator" in ids, (
        f"agent-creator SkillCapability missing; got skill caps: {sorted(ids)}"
    )
    ac = next(c for c in skill_caps if c.id == "agent-creator")
    assert ac.defer_loading is True, "agent-creator 应 defer(按需 load_capability)"
    assert ac.skill is not None and ac.skill.name == "agent-creator"
    # skill body 可读(get_instructions 非空 → load_capability 能注入正文)
    body = ac.get_instructions()
    assert body and "agent" in body.lower(), f"skill body 空/异常: {body[:60]!r}"


# ═════════════════════════════════════════════════════════════════════
# T3 verify(2):queen tools.allow=['create_agent'] → ToolBridge 暴露 v2_create_agent
# ═════════════════════════════════════════════════════════════════════
def test_queen_tool_bridge_exposes_v2_create_agent():
    """queen tools.allow=['create_agent'] → ToolBridgeCapability tool_allow 透传 →
    get_toolset() 暴露 v2_create_agent(allow 在 prefixed 前 filter,无双前缀 RK11)。"""
    from src.harness.routes import assemble_capabilities

    spec = _resolve_queen_spec()
    assert spec.tools.allow == ["create_agent"], f"queen tools.allow: {spec.tools.allow}"

    caps, _ = assemble_capabilities(
        spec,
        agent_id_for_scope="queen",
        session_id="queen-test",
        harness_id="queen-test-harness",
        emitter=_mock_emitter(),
        session_key="agent-os-v2:queen-test",
        cwd_scope=[],
    )

    bridges = [c for c in caps if isinstance(c, ToolBridgeCapability)]
    assert len(bridges) == 1, f"expect exactly 1 ToolBridgeCapability, got {len(bridges)}"
    bridge = bridges[0]
    # tool_allow 透传(spec.tools.allow → ToolBridgeCapability.tool_allow)
    assert bridge.tool_allow == ["create_agent"], (
        f"tool_allow 未透传: {bridge.tool_allow}"
    )

    # get_toolset → v2_create_agent(allow filter 在 prefixed 前,无 v2_v2_ 双前缀)
    from pydantic_ai._run_context import RunContext
    from pydantic_ai.models.test import TestModel
    from pydantic_ai.usage import RunUsage

    ctx = RunContext(deps=None, model=TestModel(), usage=RunUsage())
    ts = bridge.get_toolset()
    names = asyncio.run(ts.get_tools(ctx)).keys()
    assert "v2_create_agent" in names, (
        f"model-visible v2_create_agent missing; got: {sorted(names)}"
    )
    assert "v2_v2_create_agent" not in names, "RK11 FAIL: double v2_ prefix"
    # 白名单生效:create_agent 之外的工具(如 workflow_run / a2a_call)不该出现
    other_v2 = [n for n in names if n != "v2_create_agent"]
    assert other_v2 == [], f"白名单失效,漏出其他工具: {sorted(other_v2)}"


# ═════════════════════════════════════════════════════════════════════
# T3 verify(3):调用链 — ToolBridge dispatch → tool_executor → create_agent
# 真在 tmp 隔离环境跑一次(不碰真实 repo 根 agents.yaml)
# ═════════════════════════════════════════════════════════════════════
def test_queen_can_call_create_agent_via_tool_executor(tmp_path: Path):
    """queen 的 tool_allow=['create_agent'] → ToolBridge dispatch 经 tool_executor
    能真调 create_agent handler,在 tmp 隔离 agents.yaml 写成功 + schema 过。

    验证 queen 上线后调 v2_create_agent 的完整链路通(PrefixedToolset.call_call
    strip v2_ → executor.execute('create_agent', ...) → handler)。此处直接验
    executor.execute(tool_allow 白名单内的唯一工具),等价 ToolBridge dispatch 后段。
    """
    # 种 tmp agents.yaml(defaults + 1 default agent,模拟 queen append 场景)
    cfg_path = tmp_path / "agents.yaml"
    seed = {
        "defaults": {"model": "glm-4.7"},
        "agents": [{
            "id": "help", "default": True, "name": "seed",
            "model": "glm-4.7", "workspace": str(tmp_path / "help"),
        }],
    }
    cfg_path.write_text(yaml.safe_dump(seed, allow_unicode=True), encoding="utf-8")

    executor = _state.tool_executor
    assert executor is not None, "tool_executor 未装配"

    new_ws = tmp_path / "coder" / "workspace"
    result = asyncio.run(executor.execute("create_agent", {
        "id": "coder",
        "name": "测试 coder",
        "model": "glm-4.7",
        "workspace": str(new_ws),
        "instructions": "测试用短指令",
        "soul_md": "# coder\n测试 L0",
        "agents_md": "## 身份\ncoder\n\n## 规则\n写代码",
        "config_path": str(cfg_path),
    }))

    assert result.get("status") == "success", (
        f"create_agent via executor 失败: {result}"
    )
    out = result.get("output", {})
    # handler 返 ok + agent_id + workspace + files_written
    assert isinstance(out, dict) and out.get("ok") is True
    assert out["agent_id"] == "coder"
    assert Path(out["workspace"]).exists()

    # agents.yaml append 成功 + 整份 schema 过
    raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    cfg = AgentsConfig.model_validate(raw)
    ids = {a.id for a in cfg.agents}
    assert ids == {"help", "coder"}, f"append 后 ids: {ids}"
    defaults = [a.id for a in cfg.agents if a.default]
    assert defaults == ["help"], "append 不该改 default(help 仍是)"

    # workspace 文件落盘
    assert (new_ws / "SOUL.md").exists()
    assert (new_ws / "AGENTS.md").exists()
