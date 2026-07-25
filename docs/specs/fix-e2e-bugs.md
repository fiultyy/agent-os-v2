# Spec: fix-e2e-bugs
Date: 2026-07-25
Status: Locked (用户授权"全修,主编排,多轮")
Iteration base: ff65b5e

## Problem
真交互式 TUI e2e(非离线 --dump)暴露 3 个 bug,离线 dump 验不出:
1. **turn 404**:TUI 对 observe 历史 session 发 `/h/.../turn` → orche 404。observe/orche session 割裂(`/v1/execute` session 不进 `_store`)。orche 重启后旧 session 全 404。
2. **raw_exec 弹窗越界**(Bug#3):`--dump --replay` 演示弹窗 width=64 切左侧 session 列表(真交互全屏 spawn 不触发)。
3. **(no cwd)**(Bug#4):agent-os-v2 session 显示无 cwd。TUI 漏合并 agent-os-v2 + orche GET sessions 返 cwd=None。

## Solution / In-scope
- turn 404 → auto-create(routes.py:626,ADR-1)
- raw_exec 弹窗 Popup::centered area 修(state.rs,ADR-2)
- no cwd 双修:orche 返 cwd + TUI 合并 agent-os-v2(ADR-3)

## Out-of-scope
- C/D 档技术债/roadmap(_memory_tools cache / yaml 注释 / orchestrate-tui 人控原语 等,非本次 e2e 暴露)
- queen 真机通电(B#4,queen grill 多轮,另迭代)

## Acceptance(节点映射)
- A(turn 404)→ observe-only session turn 不再 404,auto-create + restore 路径不破坏
- B(raw_exec)→ --dump --replay raw-exec 弹窗不切 session id
- C(no cwd)→ 真机 TUI agent-os-v2 session 显示真 cwd
- 每节点 out 后主 session tmux 真交互 TUI e2e 复验(用户要求)

## Defer 预判
- orche cwd 持久化方案(_store 加列 vs GET 查 active_cwd)由节点内自决最小(ADR-3 留两条路)
- auto-create 的 default agent 语义折衷(历史 session 续聊用 default)记 ADR-1 Consequences
