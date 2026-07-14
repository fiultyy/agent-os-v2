# Spec: TUI 按 web 布局 rebuild(第一轮:基本布局)

> Date: 2026-07-14 | topic: tui-web-rebuild | base: 2283f77 | Status: Draft
> 前序:[[harness-bridge-orchestrator]] 基础控件层 2 批(10 控件已就绪)| 配套 ADR:[[tui-web-rebuild]]

## 1. Background
当前 TUI(apps/tui-rs)是 3 panel(`Flow`/`Stack`/`Control`)用 `tab` 键循环,顶栏是 `panel.label()` 纯文本(非真 TabBar),无侧栏、无鼠标点击切页。web(apps/web,Next.js app router)是 7 个独立功能页(home/agents/canvas/flows/observe/memory/login)。要把 TUI 按 web 布局 rebuild:TabBar 分页 + 鼠标点击,复用已完成的 2 批基础控件(components/{tabs,popup,mouse,anchor,scrollbar,position,markdown,split}.rs)。

## 2. Goals (In-Scope)
- **TabBar 4 tab**:`Home / Flows / Observe / Control`(对应 web 核心功能;复用 `components/tabs.rs`)
- **鼠标点击切 tab**:`ClickMap` 命中 tab 区域 + `MouseCursor` 指示(复用 `components/mouse.rs`)
- **Flows/Observe/Control 迁移现有** `draw_flow`/`draw_stack`/`draw_control` 内容(不重写业务渲染)
- **Home tab 占位**(标题 + 提示;真实 dashboard defer)
- **键盘兼容**(Tab/数字键 切 tab,沿用现有 handle_base_key 模式)

## 3. Out-of-Scope(defer)
- `state.rs` 业务机制(sessions/flows/observe/turn/spawn/do_turn/do_spawn/refresh_flows 等)**不改**
- Agents/Canvas/Memory tab(第一轮不在 TabBar)
- Home dashboard 真实内容
- 复杂控件深度接入(scroll/split/markdown 进 tab 内容)
- 高级鼠标交互(拖拽 tab 重排 / 右键菜单 / tab 内 split)
- widgets_demo(`--widgets`)**不动**(独立演示)

## 4. User Stories
- 作为用户,我鼠标点 TabBar 切换 Home/Flows/Observe/Control,看到对应内容
- 作为用户,Tab/数字键 也能切 tab(键盘兼容)
- 作为用户,鼠标移动有光标指示(MouseCursor)

## 5. Constraints (ADR)
- [ADR-1] TabBar = Home/Flows/Observe/Control,复用 components/tabs.rs
- [ADR-2] 鼠标交互复用 components/mouse.rs(ClickMap tab 命中 + MouseCursor)
- [ADR-3] 只改 UI 层(render.rs + Panel enum + events 分发),state.rs 业务方法不动
- [ADR-4] 现有 draw_flow/draw_stack/draw_control 迁移到对应 tab,不重写

## 6. Acceptance (节点映射)
- 节点 A(TabBar 布局壳 + 鼠标接入):cargo build 0 error + cargo test 全绿(现有 38 不破)+ `--dump` 渲染 4 tab + 鼠标点击切 tab 逻辑(skeptic 验)+ state.rs 业务方法未改(grep 对比)

## 7. Open Issues
(无 —— Q1 已锁定 4 tab 范围)

## 8. Defer 预判
- Agents/Canvas/Memory tab 内容(下轮)
- Home dashboard 真实内容(下轮)
- scroll/split/markdown 接入 tab 内容(下轮)
- state.rs 业务 tab 化(下轮)
- 鼠标高级交互(拖拽/右键)(下轮)
