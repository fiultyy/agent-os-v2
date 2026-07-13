# Spec: Harness-Bridge Orchestrator(v2 薄原语层 + ratatui TUI)

> **定位**:唯一 bridge **harness 级别**(非 chat 级)的编排框架 — 统一控制面触发/管理多 harness turn,自带观测 + 交互 + raw 终端,**所见即所得**。
>
> 日期:2026-07-14 | base commit: 303429e | 前序迭代:[[multi-harness-observe]](../adr/multi-harness-observe.md) | 配套 ADR:[[harness-bridge-orchestrator]](../adr/harness-bridge-orchestrator.md)

---

## 1. 目标与定位

**问题**:
- 现有 harness 桥接(claw ACP / claude code)都停在 **chat 级**或**单 harness**,无统一控制面
- session 切换 / 多实例并行在原 harness 里**不便捷**(claw 多 session、claude code 同 session 多实例都支持但 UI 缺)
- 观测分散,turn 过程不可见;且 observe 与 harness 直连后"两客户端抢事件"

**v2 定位**:orchestrator 降为 **harness 之上的薄原语层**(2 次薄封装:claw / claude code),作为原语 — 未来复杂编排(agent graph/skills/DAG)在其上重建,现有 agent graph 保留暂不用,memory REST 保留为独立数据服务(不驱动编排)。observe 退为**纯观测数据服务**(只收)。统一控制面 + 观测 + 交互 + raw,**所见即所得**。

**核心价值主张**:harness-level bridge(非 chat)+ 自带观测 + 交互 + raw,**所见即所得**。

---

## 2. 范围(分阶段)

### P0 — 后端原语层 + observe 纯化(可 curl 验证,不依赖前端)
- orchestrator 薄封装原语 API:`trigger_turn` / `session CRUD` / `spawn_instance` / `switch_session`
- orchestrator 成为**唯一 harness 客户端**:openclaw(WS `sessions.send` 发 + subscribe 收 + 映射推 observe)+ claude code(PTY spawn `claude --resume`)
- observe 移除 `/send` + gateway `send_message`(纯观测),gateway 只留 connect + 事件映射
- 多 session / 多实例并行(无锁,harness 自处理并发)
- **e2e**:curl `trigger_turn` → orchestrator 发 harness turn → 映射推 observe → observe 收完整事件序列

### P1 — ratatui TUI + 控制(rebuild;基线简版三视图,分层重构 defer P2)
- 前端 rebuild:ratatui(Rust)TUI — flow 横向轨道 / stack 纵向 / popup 鼠标拖拽 三 demo 实证选型
- 布局化画布(session/实例树 + flow 横向轨道流 + stack),非 react-flow 节点图(TUI 做不出拖拽节点图)
- 控制栏:session 创建 / 切换 / 触发 turn / spawn(接 orchestrator P0 原语 REST)
- TUI 接 observe 实时(WS subscribe)+ 历史(REST replay)
- 单 binary 跨平台(cargo build --release → v2-tui,零依赖任意终端跑)+ 本地优先(TUI + orchestrator/observe 同机)
- **基线简版**:扁平三视图(flow/stack/popup),分层重构(Kitty 多窗口/事件/状态/渲染四层 + 组件生态 + 降级)**defer P2**(ADR-10)

### P2 — raw exec + 多实例深耕 + 编排原语升级 + TUI 分层重构
- raw 终端:TUI exec 子进程(选 session → 全屏 spawn `claude --resume`/claw TUI,ctrl+d 回),完全甩 web 终端栈(xterm.js/ws/node-pty/ttyd/tmux 全免)
- 多实例并行深耕(claude code 同 session 多实例 / claw 同 agent 多 session)
- 持久化 = harness resume(`claude --resume <sid>` / claw session key 重连),PTY 死 → 重 spawn --resume 接回
- 复杂编排原语(turn 链 / 分支 / DAG)在 P0 原语上重建
- **TUI 分层重构**(ADR-10):P1 扁平三视图 → 4 层架构(Kitty Layout 原生多窗口 / 事件层 / 状态层 / 渲染层)+ 组件集成(tui-popup 可拖拽 / ratatui-interact 模态 / ratatui-image icat / rat-event)+ Kitty 多窗口接入(ctrl+shift+enter 原生 split/stack/tab)+ 弹窗栈/z-index/鼠标路由 + 降级检测(非 Kitty 关图/降刷/单窗口 tab)
- 远程 SSH 接入

### P3+(远期,不阻塞当前)
- Tauri 桌面壳(**defer 评估**:ratatui TUI 已单 binary 4.1MB 终端原生跨平台,Tauri 壳三方案 — custom backend 重写终端渲染器 / pty+xterm.js(违 ADR-7 甩 web 栈)/ crossterm-pty(仍需渲染层回 xterm)— 均与 ADR-7/ADR-9(d) 冲突或低价值;若重引入需复议 ADR-9(d) transport 作废)
- memory REST 作为独立数据服务(不驱动编排,仅数据)— **已满足**(/v1 REST 解耦 /h harness,ADR-1 实证)

---

## 3. 架构总览

```
┌─ ratatui TUI(Rust,单 binary,rebuild)────────────────────────────┐
│  ┌ session/实例树 ┐  ┌ flow 画布(横向轨道流)──────────────────┐ │
│  │ claw           │  │  sess-A: tick_started→tool→...→done      │ │
│  │  ├ A (active)  │  │  sess-B: ...(并行)                       │ │
│  │  ├ B           │  │  tool 分支 ├─/└─ 自展开向下              │ │
│  │  └ C           │  └──────────────────────────────────────────┘ │
│  │ claude-code    │  ┌ stack 视图(纵向,observe 事件 list)──────┐ │
│  │  ├ X (×2 实例) │  └──────────────────────────────────────────┘ │
│  │  └ Y           │  ┌ raw 终端(exec 子进程,ctrl+d 回)────────┐ │
│  └────────────────┘  │ $ claude --resume <sid>  / claw TUI       │ │
│  控制栏:创建/切换/trigger_turn/spawn(REST → orchestrator)      │ │
└──────┬─────────────────────────────────┬─────────────────────────┘
       │ 控制(薄原语,REST)             │ 观测(纯,WS/REST)
┌──────▼──────────────────────┐   ┌────────▼──────────────────────┐
│ orchestrator(薄原语层)       │   │ observe(纯观测,只收)         │
│ · POST /h/{type}/sessions    │   │ · WS /ws/ingest(收 orche 推) │
│ · POST .../turn(trigger)     │   │ · WS /ws/subscribe            │
│ · POST .../spawn(instance)   │   │ · REST replay/registry        │
│ · POST /switch(active)       │   │ · /send 移除(纯)            │
│ · DELETE .../sessions/{id}   │   │ · gateway 只 connect+事件映射 │
│                              │   │   (send 逻辑已搬 orchestrator) │
│ 【唯一 harness 客户端】       │   │ 【不连任何 harness】          │
│  claw  WS sessions.send+sub  │   │  只收 orche 推(经 /ws/ingest)│
│  claude PTY spawn --resume   │   │                               │
└──────┬───────────────────────┘   └───────────────────────────────┘
       │ WS sessions.send / PTY spawn           ▲ 事件(WS 推 orche→observe)
       │ 服务端→服务端(Python WS/subprocess)   │ 无 CORS(TUI 无浏览器)
┌──────▼─────────────────────────────────────────┴──────────────────┐
│ harness(orchestrator 唯一连):                                   │
│   openclaw(:18789 WS,同 agent 多 session)+                       │
│   claude code(PTY spawn --resume,同 session 多实例无锁)          │
└───────────────────────────────────────────────────────────────────┘
外部:LM Studio :16666(embedding)| 智谱 LLM(glm-4.7)— 仅 memory 用
```

**关键纠正(三条铁律)**:
1. **orche 是唯一连 harness 的客户端**(发 claw `sessions.send` + 收事件 + 映射推 observe;spawn claude PTY + 解析 stream-json 推 observe)
2. **observe 不连任何 harness**,只收 orche 推(经 `/ws/ingest`)
3. **无 CORS**:TUI 无浏览器;orchestrator 回控 claw/claude 是服务端→服务端(Python WS/subprocess,不执行浏览器同源策略)

---

## 4. orchestrator 薄原语层

**原语 API**(REST,orchestrator 直连 :8001):

| 方法 | 路径 | 语义 |
|------|------|------|
| POST | `/h/{type}/sessions` | 创建 session(body: agent_id?, cwd?) |
| GET | `/h/{type}/sessions` | 列 session |
| POST | `/h/{type}/sessions/{id}/turn` | 触发 turn(body: message)— 核心原语 |
| POST | `/h/{type}/sessions/{id}/spawn` | spawn 新实例(claude code 同 session 多实例) |
| POST | `/switch` | 切 active session(前端焦点 = 发送目标,非锁) |
| DELETE | `/h/{type}/sessions/{id}` | 关闭 session |

`{type}` ∈ {`claw`, `claude-code`}

**harness 客户端**(orchestrator 内,唯一连 harness):
- **openclaw**:连 gateway :18789 WS,v4 握手(challenge→connect→hello-ok)+ `sessions.send` 发 + subscribe 收。**connect/事件映射/send 逻辑搬自 `observe/gateways/openclaw.py`**(observe 只留 connect + 事件映射,send 搬 orchestrator)
- **claude code**:PTY spawn `claude --resume <sid>`(或新 session `claude -p`),stream-json 解析,推 observe

**无锁**:orchestrator **不加事务锁**。多 turn / 多实例并发由 harness 自处理(claw gateway 原生支持 / claude code 多 PTY 各自 --resume)。orchestrator 只路由 + 观测。

---

## 5. observe 纯化

| 改动 | 内容 |
|------|------|
| 移除 | `app.py /send` stub + gateway `send_message` 逻辑 |
| 保留 | WS `/ws/ingest`(收 orche 推)/ `/ws/subscribe` / REST `/sessions/*/events`(replay)/ session registry / event_store |
| gateway 拆分 | `openclaw.py`/`claude_code.py`:**connect + 事件映射留 observe**(只收),**send 逻辑搬 orchestrator** |

observe 职责单一:**只收**(ingest → persist → broadcast → replay)。**不连任何 harness**,所有驱动能力归 orchestrator,避免两客户端抢事件。

---

## 6. 前端(ratatui TUI + Kitty 终端,Rust,rebuild;分层架构)

**推翻现有前端**(canvas.live / /observe web 不成熟),用 **ratatui**(Rust)TUI + **Kitty** 终端 rebuild。

**选型理由**(Bubble Tea vs ratatui 三 demo 实证对比后选 ratatui;终端选 Kitty):
- 弹窗 overlay:ratatui `Clear + Block` 原生 widget(BT 需手搓背景覆盖)
- 鼠标 `Down/Drag/Up`:crossterm 语义直接(BT 间接)
- 性能:Rust 无 GC + immediate diff > Go BT;**5ms 冷启动 / 0.1ms 每帧 draw**
- 分发:单 binary 跨平台零依赖
- **Kitty 终端**:GPU 渲染(OpenGL 平滑滚动/低延迟)+ **原生多窗口 layout**(ctrl+shift+enter split/stack/tab,Kitty 自管,非应用层模拟)+ `icat` 图形协议(observe 截图/thumbnail 终端内原生显示)
- **排除 Web/Electron**:关注加载/切换性能 — Web 冷启动慢、Electron 内存高、浏览器栈冗余

demo 实证见 `apps/tui-rs/`(flow 横向轨道 + 分支自展开 + stack 纵向 + popup 鼠标拖拽,cargo build 通过 + dump 验证);BT 版 `apps/tui/` 保留作对比 reference,非主线。

**分层架构**(P2 分层重构,P1 基线为扁平三视图过渡;详见 ADR-10):

```
┌─ Kitty Layout 层(终端原生多窗口)──────────────────────────────────┐
│  ctrl+shift+enter split/stack/tab — Kitty 自管窗口栈,TUI 不模拟      │
│  (非 Kitty 降级:单窗口 + 内部 tab)                                 │
└────────────────────────────────────────────────────────────────────┘
        │ 事件 / Resize
┌───────▼───────────────────────────────────────────────────────────┐
│ 事件层(crossterm event poll)                                       │
│  KeyEvent → focused panel                                          │
│  MouseEvent(Down/Drag/Up/Scroll)→ 点击/拖拽/滚轮                  │
│  Resize → 重算 layout    事件路由:弹窗栈顶 modal 优先消费          │
└───────┬───────────────────────────────────────────────────────────┘
        │
┌───────▼───────────────────────────────────────────────────────────┐
│ 状态层(App State,自有 event loop)                                 │
│  Panel 管理(open/focus/z-index 栈)                                │
│  弹窗栈:modal 栈顶消费所有事件,下层 panel 冻结                    │
│  数据 model(session/turn/event 缓存)                              │
└───────┬───────────────────────────────────────────────────────────┘
        │
┌───────▼───────────────────────────────────────────────────────────┐
│ 渲染层(ratatui immediate-mode)                                    │
│  Clear 弹窗遮罩  z-order:base panels → overlays → modal popup     │
│  弹窗 Rect 任意坐标:centered / absolute / offset                  │
│  ratatui-image:Kitty icat 图片渲染(降级占位)                      │
└────────────────────────────────────────────────────────────────────┘
        ▲ 组件层(4 crate):tui-popup(可拖拽)/ ratatui-interact(右键/模态 PopupDialog)
                            ratatui-image(icat/降级)/ rat-event(Dialog 事件优先级)
```

**弹窗鼠标方案**(按复杂度递进,详见 ADR-10):
- **普通弹窗**:`Clear + Rect + area.contains` 手动点击检测
- **可拖拽弹窗**:tui-popup(mouse_down/drag/up)
- **模态弹窗**:ratatui-interact `PopupDialog` + rat-event `Dialog` qualifier 拦截(栈顶消费所有事件,下层冻结)
- **右键菜单**:ContextMenu
- **z-index**:绘制顺序决定(后画在上),弹窗栈维护栈序

**降级策略**:
- 检测终端能力(`$TERM` / `KITTY_WINDOW_ID` / Kitty graphics protocol query)
- 非 Kitty/Alacritty:关图片渲染(ratatui-image 占位)+ 降刷新率 + 提示切换终端
- 非原生多窗口终端:单窗口 + 内部 tab(应用层 tab 替 Kitty layout)

**TUI 组件**(布局化画布,非 react-flow 节点图 — TUI 做不出拖拽节点图):
- **session/实例树**:左栏,harness 分组,active 高亮,实例数(×N)
- **flow 画布**:横向轨道流(turn 节点沿时间轴左→右,tool 分支 `├─/└─` 自展开向下)
- **stack 视图**:纵向堆叠(observe turn 事件 list)
- **tab 切** flow/stack,鼠标拖拽弹窗/节点
- **raw 终端**:TUI exec 子进程(选 session → 全屏 spawn `claude --resume`/claw TUI,ctrl+d 回),完全甩 web 终端栈(xterm.js/ws/node-pty/ttyd/tmux 全免)
- **控制栏**:创建/切换 session、trigger_turn、spawn(接 orchestrator P0 原语 REST)

**位置**:`apps/tui-rs/`(Rust + ratatui + crossterm)。P1 基线已实施(ratatui v0.28 + crossterm,flow/stack/control 三视图 + popup 鼠标拖拽 demo `src/bin/popup.rs`;main.rs 接 orche `/h/claw|claude-code/sessions/turn` + observe `/sessions/openclaw/.../events`)。

**形态**:**单 binary 跨平台**(cargo build --release → v2-tui,零依赖任意终端跑;Kitty 基线,非 Kitty 降级)+ **本地优先**(TUI + orchestrator/observe 同机 REST/WS)+ 远程 SSH(P2+)。

---

## 7. 多 session / 多实例并行模型

| harness | 并行能力 | orchestrator 角色(唯一客户端) |
|---------|---------|-------------------------------|
| **claw** | 同 agent 多独立 session 并行 turn(gateway 原生支持) | 路由 `sessions.send` 到各 session + 观测(复合键隔离) |
| **claude code** | 同 session 多实例无锁驱动(各自 PTY,共享 session resume) | spawn 多 PTY `claude --resume <sid>` + 观测 |

**active session** = 前端焦点状态(控制发送目标),**非锁**。观测可独立选查看(与 active 解耦)。

**无锁原则**:并发由 harness 处理,orchestrator 不引入协调开销。active(前端焦点)与观测(查看)解耦。

---

## 8. 验证策略

| 阶段 | 验证 |
|------|------|
| **P0** | curl `trigger_turn` → orchestrator 发 harness turn → 映射推 observe → observe 收完整事件序列(tick_started→...→tick_completed)+ 多 session 隔离 + spawn 多实例 + switch active。连真 claw(:18789)+ claude code PTY。不依赖前端 |
| **P1** | ratatui TUI 渲染 flow/stack/popup(三 demo 验证)+ 接 orchestrator 控制栏触发 + session 切换 + 接 observe 实时/历史 |
| **P2** | raw exec 子进程(全屏 spawn,ctrl+d 回)+ 多实例并行深耕 + 编排原语(turn 链 / 分支)+ claude --resume / claw 重连行为实测 + TUI 分层重构(4 层架构 + Kitty 多窗口 + 组件 + 降级检测) |
| **qa-test** | 新 intent 覆盖:thin-orchestration / raw-terminal / multi-instance / session-switch |
| **对抗验证**(longline-ultracode) | 每阶段 workflow fan-out 实施 + skeptic 审查 + 主 session 真端到端跑(挤出假阳性) |

---

## 9. 不在本迭代范围(defer)

- **Tauri 桌面壳**(P3+ **defer**:评估后 ratatui 终端原生单 binary(4.1MB)已足,Tauri 壳三方案均与 ADR-7 甩 web 栈 / ADR-9(d) transport 作废 冲突或低价值;若重引入需复议 ADR-9(d),消除"transport 留口"矛盾)
- **memory REST 重构**(已满足:/v1 REST 解耦 /h harness,ADR-1 memory 独立数据服务实证;无需拆 service)
- **现有重编排(agent graph/skills/DAG)重建**(P2+ 在 P0 原语上做 — agent graph 保留暂不用)
- **token_delta 节流 / observe 容器化**(继承 multi-harness-observe defer)

---

## 关联

- 前序:[[multi-harness-observe]](../adr/multi-harness-observe.md)— observe-service 已建(:8002),本迭代纯化(移除 /send + gateway send)+ 加薄编排层
- 配套 ADR:[[harness-bridge-orchestrator]](../adr/harness-bridge-orchestrator.md)
- 实证产物:`apps/tui-rs/`(ratatui demo)/ `apps/tui/`(BT reference)/ `services/observe`(:8002)/ `services/orchestrator`(:8001)/ openclaw gateway(:18789,外部)
