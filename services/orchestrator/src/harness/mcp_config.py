"""MCP server 配置 → ``MCPToolset`` 解析器(5B)。

支持三传输(stdio / sse / streamable_http),配置格式见
``docs/mcp-config-template.md``。``pydantic-ai.mcp.MCPToolset`` 是 pydantic-ai 2.12
推荐入口,内部用 ``fastmcp.Client`` 走 MCP 协议(tools/resources/sampling/elicitation)。
Agent.run 用 ``AsyncExitStack.enter_async_context(toolset)`` 自动 connect/disconnect,
调用方无需手动管理生命周期(见 native_agent.build_native_agent docstring)。

实现照搬 pydantic-ai ``load_mcp_toolsets`` 的 stdio/http 分支语义
(``StdioTransport(command,args,env,cwd)`` / ``MCPToolset(url, headers=...)``),
但接受 *list[dict]* 输入而非 Claude Desktop JSON,便于 agent 配置内联携带
(``_state.agents[id]["mcp_servers"]``)。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Sequence

from pydantic_ai.mcp import MCPToolset

_VALID_TRANSPORTS = ("stdio", "sse", "streamable_http")


def _build_stdio(spec: dict[str, Any]) -> MCPToolset:
    # 局部 import:StdioTransport 仅在 fastmcp 装好时可用,延后到首次 stdio 配置才 import
    # 避免无 MCP 依赖环境 import 本模块即崩。
    from fastmcp.client.transports import StdioTransport

    if "command" not in spec:
        raise ValueError(f"MCP stdio server {spec.get('name')!r} missing 'command'")
    transport = StdioTransport(
        command=spec["command"],
        args=list(spec.get("args") or []),
        env=spec.get("env") or None,
        cwd=str(spec["cwd"]) if spec.get("cwd") is not None else None,
    )
    return MCPToolset(transport, id=spec.get("name"))


def _build_http(spec: dict[str, Any], transport_hint: str) -> MCPToolset:
    """sse / streamable_http 共用:URL 直传 MCPToolset。

    pydantic-ai ``MCPToolset(url)`` 按 URL 自行推断 SSE vs streamable_http
    (``infer_transport_type_from_url``);``transport_hint`` 只在校验 schema 时用,
    不强制改 URL 解析。如需 headers/auth 等额外 kwargs,后续按需透传(ponytail:YAGNI now)。
    """
    if "url" not in spec:
        raise ValueError(
            f"MCP {transport_hint} server {spec.get('name')!r} missing 'url'"
        )
    headers = spec.get("headers")
    if headers is not None:
        return MCPToolset(spec["url"], id=spec.get("name"), headers=headers)
    return MCPToolset(spec["url"], id=spec.get("name"))


def build_mcp_toolset(spec: dict[str, Any]) -> MCPToolset:
    """单条 MCP server 配置 → ``MCPToolset``(不 prefix)。

    spec 字段:
      - ``transport``:``"stdio"`` | ``"sse"`` | ``"streamable_http"``(必填)
      - ``name``:可选(toolset id + prefix 用)
      - stdio:``command`` 必填;``args``/``env``/``cwd`` 可选
      - sse/streamable_http:``url`` 必填;``headers`` 可选
    """
    transport = spec.get("transport")
    if transport not in _VALID_TRANSPORTS:
        raise ValueError(
            f"MCP server {spec.get('name')!r} transport must be one of "
            f"{_VALID_TRANSPORTS}, got {transport!r}"
        )
    if transport == "stdio":
        return _build_stdio(spec)
    return _build_http(spec, transport)


def build_mcp_toolsets(
    specs: Sequence[dict[str, Any]],
) -> list[Any]:
    """配置列表 → PrefixedToolset 列表(供 ``Agent(toolsets=...)`` 直接吃)。

    每条 spec 经 ``build_mcp_toolset`` 造 ``MCPToolset``,再用 ``.prefixed(name)``
    包一层防跨 server 工具重名。``name`` 缺省时回退 ``"mcp<i>"``。
    错误配置抛 ``ValueError``(调用方决定降级 vs fail-fast;``build_native_agent``
    目前 fail-fast——错误 MCP 配置应早暴露而非静默吞)。
    """
    toolsets: list[Any] = []
    for i, spec in enumerate(specs):
        ts = build_mcp_toolset(spec)
        name = spec.get("name") or f"mcp{i}"
        toolsets.append(ts.prefixed(name))
    return toolsets


def load_global_mcp_servers(config_path: str | Path | None = None) -> list[dict[str, Any]]:
    """读全局 ``.mcp.json`` → list[dict] 规范化(ponytail:全局 fallback 源)。

    兼容两种格式:
      - Claude Desktop / Cursor 原生 ``{"mcpServers": {name: {...}}}``(无 transport
        字段;按 ``command`` / ``url`` 推断);转 list 时补 ``transport`` + ``name``。
      - 本仓库 list 格式 ``[{"name": ..., "transport": ..., ...}, ...]`` 直传。

    文件不存在 / 读取失败 → 返 ``[]``(调用方按需把空 list 与显式 ``mcp_servers=None``
    区分:agent 配置非空 → 优先;否则 fallback 全局空 = 不挂)。

    config_path:默认按 env ``AO2_MCP_CONFIG`` / cwd ``.mcp.json`` 找(ponytail:env
    覆盖路径,默认 cwd 旁),允许测试用 tmp_path 注入。
    """
    path = Path(config_path) if config_path else Path(
        os.getenv("AO2_MCP_CONFIG", ".mcp.json")
    )
    if not path.exists():
        return []
    try:
        raw = path.read_text(encoding="utf-8")
        import json

        data = json.loads(raw)
    except Exception:
        return []

    # 本仓库 list 格式:直传(逐项补 name/transport 缺省)。
    if isinstance(data, list):
        out: list[dict[str, Any]] = []
        for i, spec in enumerate(data):
            if not isinstance(spec, dict):
                continue
            s = dict(spec)
            s.setdefault("name", spec.get("name") or f"mcp{i}")
            if "transport" not in s:
                if "command" in s:
                    s["transport"] = "stdio"
                elif "url" in s:
                    s["transport"] = "streamable_http"
            out.append(s)
        return out

    # Claude Desktop / Cursor 原生格式:{"mcpServers": {name: {...}}}。
    if isinstance(data, dict):
        servers = data.get("mcpServers")
        if not isinstance(servers, dict):
            return []
        out2: list[dict[str, Any]] = []
        for name, srv in servers.items():
            if not isinstance(srv, dict):
                continue
            s = dict(srv)
            s["name"] = name
            if "command" in s:
                s.setdefault("transport", "stdio")
            elif "url" in s:
                s.setdefault("transport", "streamable_http")
            else:
                # 无 command/url 的 entry 跳过(load_mcp_toolsets 原版会 raise,
                # 这里 best-effort 不让一条坏配置废掉所有全局 server)。
                continue
            out2.append(s)
        return out2
    return []
