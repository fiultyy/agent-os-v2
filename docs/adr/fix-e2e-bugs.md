# ADR: fix-e2e-bugs
Date: 2026-07-25
Status: Active
Iteration base: ff65b5e

## ADR-1: turn 404 → auto-create(orche)
Status: Accepted
Context: 真交互 TUI 对 observe 历史 session 发 `POST /h/{type}/sessions/{id}/turn` → orche 404(routes.py:626)。根因:`/v1/execute` 创建的 session 不进 harness `_store`,但 observe 收了 event;TUI `fetch_sessions` 从 observe 拉到这些 session 列出。orche 重启后(`_sessions` 内存丢)或 `/v1/execute` 来源 session(`_store` 无)→ turn 端点 `_sessions` 无 + `_store` 无 → 404。真机 e2e 暴露(离线 `--dump --replay` 验不出,因 dump 不发真 turn)。
Decision: `trigger_turn` 端点 session 不存在(`_sessions` 无 + `_store` 无)时 **auto-create**(调 `_build_native_session(default agent_id)` + `_store.create`)而非 raise 404。复用 `create_session`(routes.py:523)的构建+落库逻辑。
Alternatives: ① 保持 404 + TUI 过滤 observe-only session(失去看历史能力) ② `/v1/execute` 也写 `_store`(改动大,跨端点统一 session 管理) ③ auto-create(选:最小改动,真机重启后旧 session 也能续聊)。
Consequences: observe-only session 首次 turn 时 auto-create 为 default agent(help)。语义折衷:历史 session 续聊用 default agent(若原 agent 非 default,agent 身份变化;但比 404 强,且 restore 路径 `_store` 有 → 重建带原 `agent_id` 不受影响)。保 restore 路径优先级(`_store` 有 → 重建带原 agent_id,不退化为 default)。
Constrains: [A.T1]

## ADR-2: raw_exec 演示弹窗越界修(TUI)
Status: Accepted
Context: `run_dump`(main.rs:344)的 raw-exec 演示弹窗 `Popup::centered(...,64,10)` width=64,在 dump 分屏渲染里 x 坐标越界延伸到左侧 session 列表区,把 `e2e-exec-383398` 切成 `e2/80/57`。真交互 `raw_exec` 是全屏 spawn(`raw_exec.rs::spawn_resume`)不触发此 bug,仅 `--dump --replay` 演示渲染暴露。
Decision: 修 `Popup::centered`(state.rs)的 area 计算 —— width 适配 terminal 宽度 / 居中 x 坐标不越出对话区。约束弹窗在可用区内。
Alternatives: ① defer(5:TUI 原判断真机全屏不触发) ② 修(选:用户要全修,且 dump 是主验证工具,输出要干净)。
Consequences: `--dump --replay` 输出 raw-exec 弹窗不再切 session 列表。`Popup::centered` 改动影响所有用它的弹窗(help 等),skeptic 必验 help 弹窗不破坏。
Constrains: [B.T1]

## ADR-3: session (no cwd) 双修(orche + TUI)
Status: Accepted
Context: Control tab session 显示 `(no cwd)`。双根因:① TUI `state.rs:87-88` `merge_orche_session_meta` 只对 `claw`+`claude-code` 合并 cwd,**漏 `agent-os-v2`** ② orche `GET /h/agent-os-v2/sessions` 返 `cwd=None`(turn 时 `_active_cwd` 没落 session 记录/`_store`)。curl 实测 keys 含 cwd 但值 None。
Decision: ① TUI `state.rs fetch_sessions` 加 `merge_orche_session_meta(&mut sg, "agent-os-v2")` ② orche 让 `GET /h/{type}/sessions` 返当前 cwd —— turn 时把 `_active_cwd` 落 `_store`(加 cwd 持久化)或 GET 时按 session_key 查 `_active_cwd` 返。
Alternatives: ① 仅 TUI 合并(orche 仍 None → 没用) ② 仅 orche 返 cwd(TUI 没合并不显示) → 双修(选:两边都补才闭环)。
Consequences: agent-os-v2 session 显示真 cwd。orche 侧 cwd 持久化(`_store` 加 cwd or GET 查 `_active_cwd`)。turn 404 auto-create(A.T1)的 default cwd 也能经此显示。
Constrains: [C.T1, C.T2]
