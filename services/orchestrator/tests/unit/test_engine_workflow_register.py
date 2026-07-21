"""W-P0-6 单测:engine.py ``_WORKFLOW_TOOLS`` 清单注册(design §5 W-P0-6)。

verify(design §5 W-P0-6 verify 行,逐字):
- ``_tool_registry.get('workflow_run')`` 命中(register 名无 v2_ 前缀)
- 经 ``ToolBridgeCapability.get_toolset`` 后模型可见名 = ``v2_workflow_run``
  (不带双 ``v2_`` 前缀 — RK11)
- dispatch 端 strip ``v2_`` 前缀回 ``workflow_run`` 不崩(空 nodes 返 R7 status=error)

红线对位:
- R1/R2/R5(本 node 零内核代码,只清单注册)— 由 workflow_engine / v2_workflow
  模块自身的红线单测覆盖,本测不重复。
- RK11(命名):核心断言点。
"""

from __future__ import annotations

import asyncio

import pytest

# ── 导入 engine 模块(通电 _WORKFLOW_TOOLS 注册)──
# engine 模块 import 会触发 src.services._state + ToolRegistry + 工具清单注册。
import src.engine as engine_mod
from src.harness.capabilities.tool_bridge_capability import ToolBridgeCapability
from src.tools.catalog import ToolLayer

_tool_registry = engine_mod._tool_registry
_tool_executor = engine_mod._state.tool_executor


# ─────────────────────────────────────────────────────────────────────
# W-P0-6 verify(1):register 名 'workflow_run' 命中(无 v2_ 前缀)
# ─────────────────────────────────────────────────────────────────────
def test_workflow_run_registered_unprefixed():
    """register 名 = 'workflow_run'(无 v2_ 前缀)— ToolBridge 自动加成 v2_workflow_run。"""
    tool = _tool_registry.get("workflow_run")
    assert tool is not None, "workflow_run not registered (engine._WORKFLOW_TOOLS missing)"
    assert "fan-out" in tool["description"].lower() or "fan-out/fan-in" in tool["description"].lower()
    # parameters schema 来自 WORKFLOW_RUN_SCHEMA(含 nodes.minItems=1)
    params = tool["parameters"]
    assert params["required"] == ["nodes"]
    assert params["properties"]["nodes"]["minItems"] == 1


def test_workflow_run_registered_at_composite_layer():
    """layer = ToolLayer.COMPOSITE(L3.3)。catalog.entries 是 dict(name → entry)。"""
    entries = _tool_registry.get_catalog().entries
    wf_entry = entries.get("workflow_run")
    assert wf_entry is not None, "workflow_run not in catalog"
    assert wf_entry.layer == ToolLayer.COMPOSITE, (
        f"workflow_run layer={wf_entry.layer} expected COMPOSITE"
    )


def test_v2_prefixed_name_not_registered():
    """RK11 反向断言:register 名必须 **不是** 'v2_workflow_run'(否则 ToolBridge
    再加成 'v2_v2_workflow_run')。"""
    assert _tool_registry.get("v2_workflow_run") is None, (
        "register 名误带 v2_ 前缀 — ToolBridge 会再加成 v2_v2_workflow_run (RK11 violation)"
    )
    assert _tool_registry.get("v2_v2_workflow_run") is None


# ─────────────────────────────────────────────────────────────────────
# W-P0-6 verify(2):经 ToolBridgeCapability.get_toolset 后模型可见 'v2_workflow_run'
# (不带双 v2_ 前缀) — RK11 核心
# ─────────────────────────────────────────────────────────────────────
def test_model_visible_name_is_v2_workflow_run_no_double_prefix():
    """RK11:ToolBridge .prefixed('v2') 自动加成 v2_workflow_run。

    若 register 名误带 v2_ 前缀,模型可见 'v2_v2_workflow_run' — 双前缀。
    """
    from pydantic_ai._run_context import RunContext
    from pydantic_ai.models.test import TestModel
    from pydantic_ai.usage import RunUsage

    ctx = RunContext(deps=None, model=TestModel(), usage=RunUsage())
    cap = ToolBridgeCapability(tool_executor=_tool_executor)
    ts = cap.get_toolset()
    names = asyncio.run(ts.get_tools(ctx)).keys()

    # 命中目标名(逐字 design §5 W-P0-6 verify 第二条)
    assert "v2_workflow_run" in names, (
        f"model-visible name missing 'v2_workflow_run'; got: {sorted(names)}"
    )
    # 不带双 v2_ 前缀
    assert "v2_v2_workflow_run" not in names, (
        "RK11 FAIL: double v2_ prefix — register 名误带 v2_ 前缀"
    )
    # 未加前缀的裸名不直接暴露给模型(必须经 v2_ 命名空间)
    assert "workflow_run" not in names


# ─────────────────────────────────────────────────────────────────────
# W-P0-6 verify(3):dispatch 端 strip 前缀回 workflow_run 不崩
# ─────────────────────────────────────────────────────────────────────
def test_dispatch_strips_v2_prefix_and_calls_handler():
    """PrefixedToolset.call_tool('v2_workflow_run', ...) strip 'v2_' 后
    dispatch 到 registry 'workflow_run' handler。

    用空 nodes 触发 R7 状态化返 {status: error}(不调 engine.run,零副作用),
    验证 dispatch 路径完整通电。
    """
    from pydantic_ai._run_context import RunContext
    from pydantic_ai.models.test import TestModel
    from pydantic_ai.usage import RunUsage

    ctx = RunContext(deps=None, model=TestModel(), usage=RunUsage())
    cap = ToolBridgeCapability(tool_executor=_tool_executor, pitfail_registry=None)
    ts = cap.get_toolset()
    tools = asyncio.run(ts.get_tools(ctx))
    tool = tools["v2_workflow_run"]

    # 空 nodes → handler 首行 model_validate 拒(RK7)→ 返 {status: error} 不 raise
    # ToolExecutor.execute 把 handler dict 包成顶层 {status: success, output: {handler dict}}
    # (handler 自身 status=error 是 handler 业务态,非 executor 失败态),ToolBridge
    # _execute_via_registry 再 str(result['output']) 给模型。
    result_str = asyncio.run(ts.call_tool("v2_workflow_run", {"nodes": []}, ctx, tool))

    # 路由到 workflow_run_handler 的证据:R7 状态化 error dict 进 result_str
    assert isinstance(result_str, str)
    assert "invalid nodes spec" in result_str, (
        f"dispatch did not route to workflow_run_handler (RK7 error msg missing); got: {result_str!r}"
    )
    assert "status" in result_str and "error" in result_str


def test_dispatch_valid_nodes_routes_through_handler():
    """合法 nodes(单 prompt)dispatch 通 — handler 会调 engine.run(P0 真路径)。

    本测只验 dispatch strip 不崩 + handler 被调用,不验 engine.run 真跑
    (engine.run 真跑会 spawn native agent,属 W-P0-7 e2e 范围)。
    monkeypatch registry handler 记录调用,返 success dict 避开真 spawn。
    """
    from pydantic_ai._run_context import RunContext
    from pydantic_ai.models.test import TestModel
    from pydantic_ai.usage import RunUsage

    called = {"count": 0}

    async def _fake_handler(nodes, fan_in="list", timeout_per_node_ms=120000, concurrency=8):
        called["count"] += 1
        called["nodes"] = nodes
        return {"status": "success", "output": {"stub": True}}

    # 临时换掉 registry handler(dispatch 经 _execute_via_registry → executor.execute
    # → registry 'workflow_run' handler)。restore 必走 finally。
    tool = _tool_registry.get("workflow_run")
    orig_handler = tool["handler"]
    tool["handler"] = _fake_handler
    try:
        ctx = RunContext(deps=None, model=TestModel(), usage=RunUsage())
        cap = ToolBridgeCapability(tool_executor=_tool_executor, pitfail_registry=None)
        ts = cap.get_toolset()
        tools = asyncio.run(ts.get_tools(ctx))
        wf_tool = tools["v2_workflow_run"]
        result_str = asyncio.run(ts.call_tool(
            "v2_workflow_run",
            {"nodes": [{"prompt": "hello"}]},
            ctx, wf_tool,
        ))
    finally:
        tool["handler"] = orig_handler

    assert called["count"] == 1, "dispatch did not reach workflow_run handler"
    assert called["nodes"] == [{"prompt": "hello"}]
    # success → str(output) = str({'stub': True})
    assert "stub" in result_str


# ─────────────────────────────────────────────────────────────────────
# 退路:_WORKFLOW_TOOLS_AVAILABLE flag(design 守护:可选 import 不阻断 engine 启动)
# ─────────────────────────────────────────────────────────────────────
def test_workflow_tools_available_flag_set():
    """engine 模块 import 成功时 _WORKFLOW_TOOLS_AVAILABLE = True(测试环境通电)。"""
    # 本测运行 = engine 成功 import = flag True。生产环境若 v2_workflow import 失败,
    # engine 仍启动(降级 _WORKFLOW_TOOLS=[]),_WORKFLOW_TOOLS_AVAILABLE=False。
    assert getattr(engine_mod, "_WORKFLOW_TOOLS_AVAILABLE", False) is True
