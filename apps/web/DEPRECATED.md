# ⚠️ DEPRECATED — apps/web

**状态:已弃用(2026-07-18)。本 web 前端(Next.js)不再维护,不要在此新增功能。**

## 原因

agent-os-v2 前端转向 TUI(`apps/tui-rs`,Rust ratatui)。web 前端随 graph loop
聚焦编排器 + pydantic-ai 2.0 作 agent 基建的重构一并弃用——见 ADR
`docs/adr/pydantic-ai-v2-adoption.md`「graph loop 聚焦编排器(2026-07-18 增补)」。

## 影响范围

- **`/v1/execute` SSE 通道**(web 唯一消费)退役。orchestrator `/execute` 端点
  **保留**(graph 执行引擎,native `/h/agent-os-v2` turn 与多 agent 编排复用)。
- gateway SSE 代理 `services/gateway/src/routes/execute.py`(web 专用,随 web 弃用
  后零消费者)**已清理删除**;`orchestrate` / `chat` 代理暂留(待 gateway 整体
  弃用决策,非「web 前端」范围)。
- **新前端**:`apps/tui-rs`(走 `/h` harness routes + observe-service 进程内通信,
  不经 HTTP SSE)。

## 不要在此新增功能
