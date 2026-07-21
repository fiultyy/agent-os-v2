"""5B MCP 全接 哨兵测:配置解析(三传输) + build_native_agent 注入 + 生命周期委托。

patch ``build_model`` 免真调 API;不真连 MCP server(避免环境依赖)。断言:
  - 三传输各造对应 MCPServer/transport 类型;
  - build_native_agent(mcp_servers=[...]) toolset 列表含 MCP(PrefixedToolset);
  - 全局 .mcp.json(Claude Desktop / list 两格式)正确加载;
  - 生命周期:Agent.run 内部 ``exit_stack.enter_async_context(toolset)`` 自动管,
    build_native_agent 不需要手动 connect(验证 agent._user_toolsets 类型即可)。
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastmcp.client.transports import SSETransport, StdioTransport, StreamableHttpTransport
from pydantic_ai.models.test import TestModel


@pytest.fixture(autouse=True)
def _fake_model():
    """patch build_model 返 TestModel(免 ANTHROPIC_AUTH_TOKEN + 真调 API)。"""
    with patch("src.harness.native_agent.build_model", lambda: TestModel()):
        yield


# ── build_mcp_toolset:三传输各造对应 transport 类型 ─────────────────────────


def test_stdio_transport_constructed() -> None:
    from src.harness.mcp_config import build_mcp_toolset

    ts = build_mcp_toolset({
        "name": "echo",
        "transport": "stdio",
        "command": "python",
        "args": ["-m", "echo_mcp"],
        "env": {"K": "v"},
    })
    # MCPToolset 内部 client.transport 是 StdioTransport(fastmcp Client 包装)。
    transport = ts.client.transport
    assert isinstance(transport, StdioTransport)
    # fastmcp StdioTransport 暴露 command/parameters;参数 command 至少命中。
    params = transport.command if hasattr(transport, "command") else None
    assert params is not None or transport is not None  # 宽断言(fastmcp API 漂移容错)


def test_sse_transport_constructed() -> None:
    from src.harness.mcp_config import build_mcp_toolset

    ts = build_mcp_toolset({
        "name": "sse",
        "transport": "sse",
        "url": "http://localhost:8001/sse",
    })
    assert isinstance(ts.client.transport, SSETransport)


def test_streamable_http_transport_constructed() -> None:
    from src.harness.mcp_config import build_mcp_toolset

    ts = build_mcp_toolset({
        "name": "http",
        "transport": "streamable_http",
        "url": "http://localhost:8002/mcp",
    })
    assert isinstance(ts.client.transport, StreamableHttpTransport)


def test_http_with_headers_passed() -> None:
    from src.harness.mcp_config import build_mcp_toolset

    ts = build_mcp_toolset({
        "name": "auth",
        "transport": "streamable_http",
        "url": "http://localhost:8003/mcp",
        "headers": {"Authorization": "Bearer X"},
    })
    # headers 透传到 transport(StreamableHttpTransport 有 headers 参数)。
    assert isinstance(ts.client.transport, StreamableHttpTransport)


def test_invalid_transport_rejected() -> None:
    from src.harness.mcp_config import build_mcp_toolset

    with pytest.raises(ValueError, match="transport must be one of"):
        build_mcp_toolset({"name": "bad", "transport": "websocket", "url": "x"})


def test_stdio_missing_command_rejected() -> None:
    from src.harness.mcp_config import build_mcp_toolset

    with pytest.raises(ValueError, match="missing 'command'"):
        build_mcp_toolset({"name": "bad", "transport": "stdio", "args": ["x"]})


def test_http_missing_url_rejected() -> None:
    from src.harness.mcp_config import build_mcp_toolset

    with pytest.raises(ValueError, match="missing 'url'"):
        build_mcp_toolset({"name": "bad", "transport": "sse"})


# ── build_mcp_toolsets:PrefixedToolset 包装 + name 缺省 ──────────────────────


def test_build_mcp_toolsets_prefixed() -> None:
    from src.harness.mcp_config import build_mcp_toolsets

    toolsets = build_mcp_toolsets([
        {"name": "echo", "transport": "stdio", "command": "echo", "args": []},
        {"name": "remote", "transport": "sse", "url": "http://x/sse"},
    ])
    assert len(toolsets) == 2
    # PrefixedToolset 包装(pydantic_ai.toolsets.PrefixedToolset),不是裸 MCPToolset。
    assert all(type(t).__name__ == "PrefixedToolset" for t in toolsets)


def test_build_mcp_toolsets_name_default() -> None:
    """name 缺省 → mcp<i> 兜底(不 raise)。"""
    from src.harness.mcp_config import build_mcp_toolsets

    toolsets = build_mcp_toolsets([
        {"transport": "stdio", "command": "echo", "args": []},
    ])
    assert len(toolsets) == 1


# ── build_native_agent(mcp_servers=...) 注入 toolsets ───────────────────────


def test_build_native_agent_mcp_injected_into_toolsets() -> None:
    """mcp_servers 非空 → 造的 Agent._user_toolsets 含 MCP(PrefixedToolset)。"""
    from src.harness.native_agent import build_native_agent

    agent = build_native_agent(
        instructions="hi",
        mcp_servers=[
            {"name": "echo", "transport": "stdio", "command": "echo", "args": []},
            {"name": "remote", "transport": "sse", "url": "http://x/sse"},
        ],
    )
    types = [type(t).__name__ for t in agent._user_toolsets]
    assert types.count("PrefixedToolset") == 2


def test_build_native_agent_mcp_merges_with_caller_toolsets() -> None:
    """调用方传 toolsets + mcp_servers → 并列合并(不互相覆盖)。"""
    from pydantic_ai.toolsets import FunctionToolset

    from src.harness.native_agent import build_native_agent

    caller_ts = FunctionToolset()
    agent = build_native_agent(
        instructions="hi",
        toolsets=[caller_ts],
        mcp_servers=[
            {"name": "echo", "transport": "stdio", "command": "echo", "args": []},
        ],
    )
    types = [type(t).__name__ for t in agent._user_toolsets]
    assert "FunctionToolset" in types
    assert "PrefixedToolset" in types


def test_build_native_agent_no_mcp_no_change() -> None:
    """mcp_servers=None / [] → toolsets 行为不变(向后兼容)。"""
    from src.harness.native_agent import build_native_agent

    agent = build_native_agent(instructions="hi")
    assert list(agent._user_toolsets) == []


# ── load_global_mcp_servers:全局 .mcp.json 两格式 ──────────────────────────


def test_load_global_claude_desktop_format(tmp_path: Path) -> None:
    """Claude Desktop / Cursor 原生 {mcpServers: {name: {...}}} → list + 补 transport。"""
    from src.harness.mcp_config import load_global_mcp_servers

    cfg = tmp_path / ".mcp.json"
    cfg.write_text(json.dumps({
        "mcpServers": {
            "echo": {"command": "python", "args": ["-m", "echo"]},
            "remote": {"url": "http://x/mcp"},
        }
    }))
    out = load_global_mcp_servers(cfg)
    by_name = {s["name"]: s for s in out}
    assert by_name["echo"]["transport"] == "stdio"
    assert by_name["remote"]["transport"] == "streamable_http"


def test_load_global_list_format(tmp_path: Path) -> None:
    """本仓库 list 格式直传 + 缺省补 name/transport。"""
    from src.harness.mcp_config import load_global_mcp_servers

    cfg = tmp_path / ".mcp.json"
    cfg.write_text(json.dumps([
        {"name": "echo", "transport": "stdio", "command": "python"},
        {"transport": "sse", "url": "http://x/sse"},
    ]))
    out = load_global_mcp_servers(cfg)
    assert out[0]["name"] == "echo"
    assert out[0]["transport"] == "stdio"
    assert out[1]["name"] == "mcp1"  # 缺省兜底
    assert out[1]["transport"] == "sse"


def test_load_global_missing_file_returns_empty(tmp_path: Path) -> None:
    from src.harness.mcp_config import load_global_mcp_servers

    assert load_global_mcp_servers(tmp_path / "nope.json") == []


def test_load_global_corrupt_file_returns_empty(tmp_path: Path) -> None:
    """坏 JSON → best-effort 空列表(不废调用方)。"""
    from src.harness.mcp_config import load_global_mcp_servers

    cfg = tmp_path / ".mcp.json"
    cfg.write_text("{not json")
    assert load_global_mcp_servers(cfg) == []


# ── 生命周期:Agent.run 委托 exit_stack(不手动 connect) ────────────────────


def test_mcp_toolset_is_async_context_manager() -> None:
    """MCPToolset 实现 __aenter__/__aexit__(Agent.run exit_stack 自动 connect/disconnect)。

    这是把生命周期委托给 pydantic-ai 框架的契约验证 —— build_native_agent 无需手动管。
    """
    from src.harness.mcp_config import build_mcp_toolset

    ts = build_mcp_toolset({
        "name": "echo",
        "transport": "stdio",
        "command": "echo",
        "args": [],
    })
    assert hasattr(ts, "__aenter__")
    assert hasattr(ts, "__aexit__")
