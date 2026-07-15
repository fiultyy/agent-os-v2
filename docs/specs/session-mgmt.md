# Spec IT2: Control session 管理(new / delete / archive / fork)

Status: Draft(post control-v2 IT1 后执行)
Date: 2026-07-16

## 1. Background

IT1(control-v2)把 Control 做成「快速切 session + 触发 turn 的输入平面」。IT2 补齐 session **生命周期管理**:新建 / 删除 / 归档 / 分叉。目标:在 Control 大纲侧对 claw/cc session 做完整 CRUD-ish,不离开 TUI。

## 2. Goals (In-scope)

1. **new**(大纲侧 `new` 按钮 → 弹窗选 claw/cc):
   - claw → 按已注册 agent 创(`POST /h/claw/sessions {agent_id}`,已有);picker 列已注册 agent。
   - cc → 按已有 cwd 创 或 新输入 cwd(`POST /h/claude-code/sessions {cwd}`,已有);picker 列已有 cwd + 自由输入。
2. **delete**(映射 raw 区域真删,分 cc/claw):
   - cc → 删 `~/.claude/projects/<slug>/<sid>.jsonl`(及 orche `_sessions` + observe session)。
   - claw → 删 gateway session(`sessions.delete` 或底层 transcript)+ orche/observe 清理。
   - `DELETE /h/{type}/sessions/{id}` 已有但只停 in-memory client → **扩成 raw 真删**。
3. **archive**(原 session 发一 turn「总结事实」):用现有 turn 原语,发固定 summary prompt,结果存为该 session 的一个 summary turn(可打 archive 标记)。
4. **fork**(原 session 另存新 id + 续新 turn):
   - **cc**:原生 `claude --resume <orig.jsonl> --fork-session` → 新 sid(完整上下文),orche 注册新 sid,后续 turn --resume 新 sid。✅ 干净。
   - **claw**:**无原生 `sessions.fork` RPC**。权宜:`sessions.compaction.branch`(从压缩检查点分支,非逐字);或上游加 fork RPC。→ claw fork 标 **partial**,先 compaction.branch,逐字 fork defer 上游。

## 3. Out-of-scope

- claw 逐字 fork(等 openclaw 上游 `sessions.fork` RPC)。
- 跨 harness fork(cc session fork 成 claw,反之)。
- session 重命名 / 合并(未来)。

## 4. Constraints (ADR stubs,IT2 P1 落实)

- **ADR-1 new**:claw 按 agent / cc 按 cwd;picker 接口(list-agents / list-cwds 需新)。
- **ADR-2 delete raw**:DELETE 扩真删——cc 删 jsonl 文件、claw 删 gateway session;确认 prompt(防误删)。
- **ADR-3 archive = summary turn**:固定 prompt(如「用要点总结本 session 至今的事实与决策」),结果入原 session 事件流;UI 标 archive 态。
- **ADR-4 fork**:cc `--fork-session`(原生,完整上下文);claw `compaction.branch`(权宜)+ `forkedFromParent` 元数据;逐字 claw fork defer。
- **ADR-5 后端缺口**:新增 `POST /h/{type}/sessions/fork`(cc 调 fork-session / claw 调 compaction.branch)、list-agents、list-cwds、raw-delete 扩展。

## 5. User Stories

- 大纲侧点 `new` → 选 claw + agent "main" → 新 session 建好、自动切过去、可即输入。
- 选中 cc session → delete(raw jsonl 删除,二次确认)→ 从列表消失。
- 选中 session → archive → 该 session 多一条 summary turn;大纲侧显 archive 标记。
- 选中 session → fork → 新 id 出现(同上下文),切到新 id 输入新 turn,原 session 不变。

## 6. Acceptance(映射编排节点,P1 定)

- new:claw(cc)按 picker 建新 session,observe 可见,可即 turn。
- delete:cc jsonl/claw gateway session 真删 + 列表移除 + 二次确认。
- archive:summary turn 落原 session 事件流。
- fork(cc):新 sid 完整上下文(继承原 history),原 session 不变。
- fork(claw):compaction.branch 产出可用分支(上下文为压缩版,标注 partial)。

## 7. Open Issues

- claw fork 逐字 vs compaction 权宜——P1 决(倾向先 compaction.branch,逐字 defer)。
- archive 结果存哪(原 session summary turn vs 独立 archive 存储)——P1 决(倾向原 session 一条 turn)。
- delete 二次确认 UI(弹窗 vs 行内 y/N)——P1 决。

## 8. Defer

- claw 逐字 fork(上游 `sessions.fork`)。
- 跨 harness fork / rename / merge。

## 附:fork 实现分析(2026-07-16)

**cc(claude-code)** — 原生干净:
- `claude --fork-session` (help: "When resuming, create a new session ID with --resume or --continue")。
- orche ClaudeClient.fork(orig_sid, first_msg):`claude --resume <orig.jsonl> --fork-session -p <first_msg> --output-format stream-json --verbose`。
  - claude 生成新 sid + 写新 jsonl(完整上下文)+ 跑首 turn;stream-json result 事件含 `session_id`(= 新 sid)。
  - orche 捕获新 sid → 注册 `_sessions["claude-code:<newsid>"]` + observe session → 后续 turn `--resume <newsid>`。
- jsonl 路径:`~/.claude/projects/<cwd-slug>/<sid>.jsonl`(cwd-slug 由 cwd 编码,如 `-home-yy-projects-agent-os-v2`)。

**claw(openclaw)** — 无原生 fork RPC:
- gateway methods 无 `sessions.fork`/`clone`(有 create/delete/compact/send/...);`sessions.compaction.branch` 存在但是 **compaction 专用**(从压缩检查点分支,历史是摘要非逐字);`forkedFromParent` 是元数据标记位。
- 权宜路径:fork → `sessions.compaction.branch`(分支 = 压缩上下文)+ 标 `forkedFromParent`;新 session key 续新 turn。逐字 fork 需上游加 RPC 或 orche 拉历史 replay(重 + 有损)→ defer。
