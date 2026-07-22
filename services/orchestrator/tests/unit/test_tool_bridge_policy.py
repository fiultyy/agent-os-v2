"""ToolBridgeCapability allow/deny policy 单测(P2:spec.tools 接通).

测 _filter 纯函数(白/黑名单,确定性)+ get_toolset 集成(spy _make_named_tool
验证过滤生效,不依赖 pydantic-ai toolset iteration API)。
"""
from __future__ import annotations

from unittest.mock import MagicMock

from harness.capabilities.tool_bridge_capability import ToolBridgeCapability


def _executor(names: list[str]):
    """mock ToolExecutor:registry.list_tools 返指定 name 列表。"""
    ex = MagicMock()
    ex.registry.list_tools.return_value = [
        {"name": n, "description": f"d-{n}",
         "parameters": {"type": "object", "properties": {}}}
        for n in names
    ]
    return ex


# ── _filter 纯函数(核心逻辑全在此)──
def test_filter_allow_whitelist():
    assert ToolBridgeCapability._filter(["a", "b", "c"], ["a", "c"], None) == ["a", "c"]


def test_filter_deny_blacklist():
    assert ToolBridgeCapability._filter(["a", "b", "c"], None, ["b"]) == ["a", "c"]


def test_filter_none_all_passthrough():
    assert ToolBridgeCapability._filter(["a", "b"], None, None) == ["a", "b"]


def test_filter_allow_and_deny_combined():
    # allow=[a,b,c] ∩ deny剔除[b] → [a,c](deny 在 allow 内进一步收窄)
    assert ToolBridgeCapability._filter(["a", "b", "c"], ["a", "b", "c"], ["b"]) == ["a", "c"]


def test_filter_allow_with_unknown_name_only_keeps_known():
    # allow 含不存在 name 'x' → _filter 只过已知;routes 侧 by_name 查 'x' 不命中
    assert ToolBridgeCapability._filter(["a", "b"], ["a", "x"], None) == ["a"]


# ── get_toolset 集成(spy _make_named_tool 验证过滤)──
def test_get_toolset_no_policy_makes_all(monkeypatch):
    import harness.capabilities.tool_bridge_capability as mod
    called = []
    orig = mod._make_named_tool
    monkeypatch.setattr(mod, "_make_named_tool",
                        lambda ex, pf, n, d, p: (called.append(n) or orig(ex, pf, n, d, p)))
    cap = ToolBridgeCapability(tool_executor=_executor(["read", "write", "search"]))
    cap.get_toolset()
    assert sorted(called) == ["read", "search", "write"]


def test_get_toolset_allow_filters_to_whitelist(monkeypatch):
    import harness.capabilities.tool_bridge_capability as mod
    called = []
    orig = mod._make_named_tool
    monkeypatch.setattr(mod, "_make_named_tool",
                        lambda ex, pf, n, d, p: (called.append(n) or orig(ex, pf, n, d, p)))
    cap = ToolBridgeCapability(
        tool_executor=_executor(["read", "write", "search"]), tool_allow=["read"],
    )
    cap.get_toolset()
    assert called == ["read"]  # 只 read 进白名单


def test_get_toolset_deny_hides_blacklisted(monkeypatch):
    import harness.capabilities.tool_bridge_capability as mod
    called = []
    orig = mod._make_named_tool
    monkeypatch.setattr(mod, "_make_named_tool",
                        lambda ex, pf, n, d, p: (called.append(n) or orig(ex, pf, n, d, p)))
    cap = ToolBridgeCapability(
        tool_executor=_executor(["read", "write", "search"]), tool_deny=["write"],
    )
    cap.get_toolset()
    assert sorted(called) == ["read", "search"]  # write 被剔除


def test_get_toolset_none_executor_empty(monkeypatch):
    cap = ToolBridgeCapability(tool_executor=None)
    ts = cap.get_toolset()  # executor None → 空 toolset,不抛
    assert ts is not None
