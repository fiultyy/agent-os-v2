# MCP server 配置模板(agent-os-v2 harness / 5B)

harness native 路径(`build_native_agent`)支持 [MCP](https://modelcontextprotocol.io)
server 注入 —— 把外部 MCP server 的工具按配置拉进 pydantic-ai Agent 的 toolsets。
三传输全覆盖:`stdio`(本地子进程)/ `sse`(Server-Sent Events)/ `streamable_http`
(MCP streamable HTTP)。

## 配置格式

每条 MCP server 配置(dict)字段:

| 字段 | 必填 | 适用传输 | 说明 |
|------|------|----------|------|
| `transport` | 是 | 全部 | `"stdio"` / `"sse"` / `"streamable_http"` |
| `name` | 否(推荐) | 全部 | 工具前缀(多 server 工具去歧义);缺省回退 `mcp<i>` |
| `command` | stdio 必填 | stdio | 可执行命令,如 `"python"` / `"npx"` |
| `args` | 否 | stdio | 命令参数 list,如 `["-m", "echo_mcp"]` |
| `env` | 否 | stdio | 子进程环境变量 dict |
| `cwd` | 否 | stdio | 子进程工作目录 |
| `url` | sse/http 必填 | sse / streamable_http | server URL,如 `http://localhost:8000/sse` |
| `headers` | 否 | sse / streamable_http | 额外 HTTP headers(如 `Authorization`) |

`sse` vs `streamable_http` 的实际协议由 pydantic-ai `MCPToolset` 按 URL 推断
(`infer_transport_type_from_url`);`transport` 字段只用于本校验层的 schema 分类。

### 示例(list 格式,本仓库内联 / agent 配置)

```json
[
  {
    "name": "echo_stdio",
    "transport": "stdio",
    "command": "python",
    "args": ["-m", "echo_mcp_server"],
    "env": {"ECHO_LANG": "zh"}
  },
  {
    "name": "remote_sse",
    "transport": "sse",
    "url": "http://localhost:8001/sse"
  },
  {
    "name": "remote_http",
    "transport": "streamable_http",
    "url": "http://localhost:8002/mcp",
    "headers": {"Authorization": "Bearer ${MCP_TOKEN}"}
  }
]
```

## 配置来源(双源,ponytail)

按优先级:

1. **Agent 配置(优先)**:`_state.agents[id]["mcp_servers"]`(list 格式),经
   `run_agent_turn` → `build_native_agent(mcp_servers=...)` 透传。子代理场景:spawn
   时把 MCP server 写进 config,只对该子代理生效。
2. **全局 `.mcp.json`(fallback)**:仓库根 `.mcp.json`(或 `AO2_MCP_CONFIG` env 指定
   路径),由 `_build_native_session` 启动时读一次,挂在所有 native session。
   兼容两种格式:
   - 本仓库 list 格式(见上例)
   - Claude Desktop / Cursor 原生 `{"mcpServers": {name: {...}}}`(自动补 `transport`)

agent 配置非空时**完全覆盖**全局源(不合并)—— 避免子代理意外继承全局 server。

## 生命周期

pydantic-ai `Agent.run` 用 `AsyncExitStack.enter_async_context(toolset)` 自动
connect/disconnect MCP server(每轮 run 进入/退出)。调用方无需手动管理 —— 配好
就调 `agent.run(...)`,连接断开由框架兜底。

## 参考

- pydantic-ai MCP 文档:`pydantic_ai.mcp.MCPToolset` / `load_mcp_toolsets`
- 实现:`services/orchestrator/src/harness/mcp_config.py`
- 注入点:`services/orchestrator/src/harness/native_agent.py:build_native_agent`
- 示例配置:仓库根 `.mcp.json.example`
