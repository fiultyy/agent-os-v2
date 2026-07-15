# ADR: IT2 session 管理(new/delete/archive/fork)

Date: 2026-07-16
Status: Active
Topic: session-mgmt
Iteration base: (待定,IT1 control-v2 完成后的 HEAD)

## ADR-1: new — claw 按 agent / cc 按 cwd
Status: Accepted
Context: 大纲侧需新建 session。claw session 由 agent 标识(agent:<a>:<conv>),cc 由 cwd(jsonl 存 ~/.claude/projects/<slug>/)。
Decision: `new` 按钮→弹窗选 claw/cc。claw→按已注册 agent 创(新 GET /h/claw/agents 列 agent);cc→按已有 cwd 创或自由输入(新 GET /h/claude-code/cwds 列已有 cwd)。底层复用现有 POST /h/{type}/sessions。
Alternatives: 仅自由输入 agent/cwd(否,picker 减少出错)。
Constrains: [T-routes, T-ui]

## ADR-2: delete — raw 真删 + modal 二次确认
Status: Accepted
Context: 现 DELETE /h/{type}/sessions/{id} 只停 in-memory client,不删 raw(jsonl/gateway session),删后重启会复活。
Decision: DELETE 扩真删——cc: ClaudeClient.delete(sid) 删 ~/.claude/projects/<slug>/<sid>.jsonl;claw: OpenClawClient.delete() 调 gateway sessions.delete 删 transcript。UI:删除前 modal 二次确认(y/N)。
Alternatives: 软删(仅停 client,否,复活);无确认(否,误删风险)。
Constrains: [T-claude, T-openclaw, T-routes, T-ui]

## ADR-3: archive = 原 session 一条 summary turn
Status: Accepted
Context: 归档需保留 session 事实摘要。
Decision: archive = 对原 session 用现有 turn 原语发固定 summary prompt(「用要点总结本 session 至今的事实、决策与未决项」),结果作为该 session 一个 tick_completed 存入事件流;大纲侧该 session 显 archive 标记。不另建存储。
Alternatives: 独立 archive 存储(否,复用事件流更简)。
Constrains: [T-routes, T-ui]

## ADR-4: fork — cc 原生 --fork-session / claw compaction.branch 权宜
Status: Accepted
Context: fork = 原 session 另存新 id + 续新 turn,原 session 不变。cc 有原生 --fork-session(完整上下文);claw 无 sessions.fork RPC。
Decision:
- cc: ClaudeClient.fork(orig_sid) 跑 `claude --resume <orig.jsonl> --fork-session -p <first_msg> --output-format stream-json`,从 result 捕获新 sid(完整上下文),注册 _sessions + observe,后续 --resume 新 sid。
- claw: OpenClawClient.fork(orig) 调 gateway `sessions.compaction.branch`(从压缩检查点分支,上下文为压缩版)+ 标 forkedFromParent。逐字 claw fork defer(等上游 sessions.fork RPC)。
Alternatives: claw replay 历史(否,重+有损);claw 文件级 transcript 拷贝(否,fragile)。
Consequences: claw fork 上下文是压缩版(标注 partial);cc fork 完整。
Constrains: [T-claude, T-openclaw, T-routes, T-ui]

## ADR-5: client 方法契约(节点 A 并行对齐)
Status: Accepted
Context: 节点 A 并行实现 claude.py/openclaw.py 的 fork+delete 方法,routes(UI 集成)按契约调。
Decision:
- ClaudeClient.fork(orig_sid, first_msg) -> {new_sid, tick_id}(跑 --fork-session + 首 turn,stream-json 捕获 new sid)。
- ClaudeClient.delete(sid) -> {deleted: bool}(rm jsonl 文件)。
- OpenClawClient.fork(orig_key) -> {new_key}(sessions.compaction.branch + forkedFromParent)。
- OpenClawClient.delete() -> {deleted}(gateway sessions.delete)。
契约节点 A 实现,节点 B routes 集成时对齐;mismatch skeptic/fix 收敛。
Constrains: [T-claude, T-openclaw, T-routes]

## ADR-6: 范围 — 只加 session 管理,不动 IT1 输入 UX / 后端 turn 原语
Status: Accepted
Context: IT1 已做 chat+input;IT2 只叠 session CRUD。
Decision: IT2 改 services/orchestrator(routes/claude/openclaw)+ apps/tui-rs(大纲 + 弹窗);不动 turn 原语、textarea、observe 采集。
Constrains: [全局]
