# openclaw ACP 接入点探查(给 T3 openclaw-gateway implementer)

> 来源:`explore-openclaw` subagent 探查 `~/tools/openclaw`(2026-07-13)。
> 作用:T3 的 handoff context,避免重复探查。技术细节 T3 implementer 自决,变更记 ADR-4。
> 迭代:multi-harness-observe / Node B / T3。

## 接入点(推荐):Gateway WebSocket Client + sessions.messages.subscribe

openclaw gateway 以 WS 运行(**端口 18789**),协议由 `packages/gateway-protocol` 定义(`GatewayFrame` 三帧联合:`req` / `res` / `event`)。
用 `@openclaw/gateway-client` 包的 `GatewayClient` 连接,`onEvent` 回调收事件,`sessions.messages.subscribe` 精确订阅目标 session。**零侵入**(不改 openclaw 源码)。

## 事件流(openclaw → observe schema 映射)

| openclaw gateway event | payload 要点 | → observe schema |
|---|---|---|
| `chat` (delta) | {runId, sessionKey, state:delta, deltaText, message, usage} | text stream(token_delta 本次 defer,可暂不映射) |
| `chat` (final) | {runId, sessionKey, state:final, message, stopReason} | `tick_completed` |
| `chat` (aborted/error) | {runId, sessionKey, state:aborted/error} | `tick_completed`(status=failed) |
| `agent` (tool) | {stream:tool, phase:start/update/result, toolCallId, name, args} | `tool_call`(start) / `tool_result`(result) |
| `session.operation` | {operation:compact, phase, sessionKey} | lifecycle(可选观测) |
| turn 开始 | (chat.send 触发,无显式 started 事件) | `tick_started`(gateway 在 chat.send 后合成) |

注:openclaw 无显式 "turn started" 事件。observe-gateway 在 `chat.send` 后合成 `tick_started`,收 chat final / agent tool 映射 tool / tick_completed。

## Session API(sessionKey ↔ observe session)

sessionKey 格式:`agent:<agentId>:<label>` 或 `agent:<agentId>:acp:binding:<channel>:<accountId>:<hash>`。
Gateway RPC:
- `sessions.list` / `get` / `create` / `patch` / `reset` / `delete` / `abort` / `resolve` / `usage`
- `sessions.messages.subscribe` / `unsubscribe`(精确订阅,scope `operator.read`)
交互:`chat.send`(发消息)/ `sessions.abort`(取消 turn)

## 关键技术决策(T3 implementer 自决,记 ADR-4 变更)

observe-service 是 **Python**(FastAPI),`@openclaw/gateway-client` 是 **TS**。T3 openclaw-gateway 二选一:
- **(a) TS 桥进程**:TS 写 openclaw-gateway(用 `@openclaw/gateway-client` 连 openclaw),WS 推 observe-service。优:用现成 TS client,协议帧成熟;劣:多一个 TS 进程 + npm 依赖管理。
- **(b) Python 自实现**:observe-service 内 Python WS 客户端直连 openclaw gateway:18789,自实现 `GatewayFrame` 协议帧(req/res/event + hello/auth + subscribe)。优:纯 Python 无 TS;劣:要手写协议帧 + 版本协商。

T3 自决(以"能跑通 openclaw turn 观测 + 交互 + 真实 session"为准)。

## 风险

- `@openclaw/gateway-client` 是否 npm 发布?monorepo 内部包 → (a) 需 vendor 源码;(b) Python 自实现不受影响。
- gateway 端口 18789 + auth token(读 `openclaw.json`),observe/gateway 需配置传递。
- `gateway-protocol` `minProtocol` / `maxProtocol` 版本协商。
- chat event payload 是 gateway 内部格式(`GatewayChatContentBlock[]`),需解析 text/thinking 块。

## 真实 session 管理(对齐 ADR-5)

openclaw `sessionKey` ↔ observe `(harness_type="openclaw", session_id=sessionKey)`。
`sessions.list` 拉真实 session 列表;`sessions.create` / `subscribe` 真实创建/订阅。
observe session registry 经 gateway 桥接 openclaw 真实 session(非 mock)。

## T3 验证(qa_test intent 雏形)

`intents/openclaw-turn-observe.md`:启动 openclaw → observe openclaw-gateway 连接 gateway:18789 → 经 observe 前端发一个 turn → observe 前端收到 `tick_started → tool_call → tool_result → tick_completed` → session 列表/切换真实(多 openclaw session)。
