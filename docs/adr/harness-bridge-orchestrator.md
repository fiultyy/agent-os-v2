# ADR: Harness-Bridge Orchestrator

> v2 唯一 harness 级别 bridge 编排框架的关键设计决策。日期:2026-07-14。配套 spec:[[harness-bridge-orchestrator]](../specs/harness-bridge-orchestrator.md)。
>
> 核心价值主张:唯一 **harness 级别**(非 chat 级)的编排框架 — 统一控制面触发/管理多 harness turn,自带观测 + 交互 + raw,所见即所得。

---

## ADR-1: orchestrator 降为薄原语层(重编排不废弃,作原语升级基础)

**Context**:orchestrator 现有重编排(agent graph / skills / 多节点 DAG / memory / canvas)。新愿景要薄封装 harness 触发/管理,但重编排能力不代表错误 — 它是未来复杂编排的基础;一刀切删除会丢资产并抬高未来重建成本。需明确"薄原语层"与"重编排"如何共存。

**Decision**:orchestrator 降为**薄原语层**(`trigger_turn` / `session CRUD` / `spawn_instance` / `switch`)。重编排**不废弃**,定位为**原语上升级**的未来基础。现有 agent graph 代码**保留暂不用**(未来在其上重建或参考)。memory REST **保留为独立数据服务**(不驱动编排,仅作数据读写)。

**Consequences**:
- P0 只做薄原语;复杂编排(turn 链 / 分支 / DAG)P2+ 在原语上重建
- 现有重编排代码暂搁(不删),降低 P0 风险,保留未来资产
- v2 定位从"重编排引擎"→"harness 控制面 + 原语层"
- memory 与编排解耦:数据服务独立存活,不阻塞原语层演进

---

## ADR-2: observe 纯观测(/send 移除 + gateway send 搬 orchestrator)

**Context**:observe 当前混了控制能力(`/send` stub + gateway `send_message`)。观测与控制混在同一服务导致职责模糊、双客户端抢事件风险、演进耦合。用户明确要 observe 做**纯观测数据服务**。

**Decision**:移除 observe `/send` + gateway send 逻辑。所有驱动(trigger / send)归 orchestrator。observe 职责单一:**只收**(ingest → persist → broadcast → replay + session registry)。observe gateway(`openclaw.py` / `claude_code.py`)拆分:**connect + 事件映射留 observe**(只收),**send 逻辑搬 orchestrator**。

**Consequences**:
- observe gateway 拆为两半:观测半(connect + 事件映射)留 observe,控制半(send)搬 orchestrator
- observe 纯净,职责单一,符合"观测数据服务"定位
- 双向(观测 + 控制)分离到两服务,各自演进
- 为 ADR-4(orchestrator 唯一 harness 客户端)扫清障碍 — observe 不再需要连 harness

---

## ADR-3: harness-level bridge 定位(非 chat-level)

**Context**:现有 harness 桥接(claw ACP / claude code wrapper)都停 chat 级或单 harness。v2 要做差异化定位 — 做别人没做的层级。

**Decision**:v2 定位 **harness 级 bridge** — 统一控制面触发/管理多 harness turn(claw + claude code),原语操作 turn/session/instance(harness 语义),**非 chat 消息桥**。观测的是 turn 事件流(tick/tool/branch,harness 语义),非 chat 消息流。

**Consequences**:
- 原语是 turn/session/instance(harness 级),非消息级
- 观测 turn 事件流(harness 语义),非 chat 流
- 差异化护城河:唯一 harness-level bridge + 自带观测 + 交互 + raw,所见即所得
- 多 harness 统一抽象:claw + claude code 同原语接口

---

## ADR-4: orchestrator 是唯一 harness 客户端(observe 不连 harness)

**Context**:旧 ADR 未明确"谁连 harness"。若 orchestrator 和 observe 都连同一 claw session 抢事件,会产生双消费、事件丢失、状态不一致。需钉死单一 harness 客户端,消除抢事件风险。

**Decision**:**orchestrator 是唯一 harness 客户端**:
- 连 claw `:18789` WS:`sessions.send` 发 + `subscribe` 收 + 事件映射推 observe
- spawn claude PTY + 解析 stream-json 推 observe

observe **不连任何 harness**,只收 orchestrator 推。事件流单向:harness → orchestrator → observe。避免 observe 和 orchestrator 两客户端连同 claw session 抢事件。

**Consequences**:
- 单一 harness 客户端 = 单一事件源,无抢事件 / 双消费 / 状态分裂
- observe 退化为纯被动接收(配合 ADR-2),架构清晰
- orchestrator 承担"事件映射"职责:claw WS 帧 / claude stream-json → 统一 observe schema
- 单点风险:orchestrator 挂则观测断流 — 由 orchestrator 自身高可用(P2+ 进程守护)兜底,不在 P0 引入
- 事件流方向明确:harness → orchestrator → observe → TUI

---

## ADR-5: 多 session / 多实例并行无锁(harness 自处理;active=焦点非锁)

**Context**:claw 同 agent 可多独立 session 并行 turn;claude code 同 session 可多实例(各自 PTY `--resume`)无锁驱动 — 这是 harness 原生能力(用户实测确认)。若 orchestrator 自加事务锁,会引入协调开销并抵消 harness 原生并发。

**Decision**:orchestrator **不加事务锁**。多 turn / 多实例并发由 harness 自处理。orchestrator 只路由 + 观测。active session = **前端焦点**(控制目标),**非锁**;观测可独立选(查看)与 active 解耦。

**Consequences**:
- 无并发瓶颈(harness 处理),不引入协调开销
- 事件流多路:observe 复合键 `(harness_type, session_id)` 隔离已支持
- 多实例(claude code 同 sid):各自 tick_id 区分,观测按实例 + session 聚合
- active/观测解耦:前端焦点切换不影响后台 turn 执行;观测任意 session 不抢控制权

---

## ADR-6: 前端 ratatui TUI(Rust,rebuild;选型实证 BT vs RA 三 demo;布局化非节点图)

**Context**:现有前端(canvas.live / `/observe` web)不成熟。用户要 TUI(通用 / 终端原生 / 无浏览器)。前端框架选型需实证而非拍脑袋 — 对比 Bubble Tea vs ratatui 三 demo(flow 横向轨道流 / stack 纵向 / popup 鼠标拖拽)后决断。

**Decision**:前端 rebuild,**ratatui(Rust)** TUI(`apps/tui-rs/`)。选型实证依据:
- ratatui 弹窗 `Clear+Block` overlay 原生、鼠标 `Down/Drag/Up` 直接、无 GC + 单 binary
- Bubble Tea 需手搓 overlay,鼠标语义弱

画布**布局化**(session 树 + flow 横向轨道流 + stack 纵向),**非 react-flow 节点图**(TUI 做不出拖拽节点图,用户接受布局化)。crossterm 事件(含鼠标)。BT 版 `apps/tui/` 保留作 reference。

**Consequences**:
- 推翻 web 前端(canvas.live / observe page),改 ratatui(Rust,`apps/tui-rs/`)
- 单 binary 跨平台零依赖(`./v2-tui`),终端原生,**无 web 栈 / CORS**
- 画布布局化:flow 横向轨道 + 分支 `├─/└─` 自展开向下;stack 纵向;popup 鼠标拖拽
- 选型留痕:BT 版 `apps/tui/` 作 reference,便于回溯决策依据

---

## ADR-7: raw = TUI exec 子进程 harness TUI(完全甩 web 终端栈)

**Context**:raw 交互必须(claw TUI 简陋 / claude code 强,raw 兜底)。原 web 方案 xterm.js + ws + node-pty + ttyd + tmux,改 TUI 后这套全成冗余 — 终端原生嵌终端即可,无需 web 终端模拟栈。

**Decision**:raw = **TUI 直接 exec 子进程 harness TUI**(选 session → 全屏 spawn `claude --resume <sid>` / claw TUI,ctrl+d 回)。终端原生嵌终端。**完全甩掉 web 终端栈**(xterm.js / ws / node-pty / ttyd / tmux 全免)。持久化交 harness resume(claude `--resume` / claw session key,见 ADR-8)。

**Consequences**:
- **完全甩掉 web 终端栈**(xterm.js / ws / node-pty / ttyd / tmux 全免)
- raw 最原生(终端嵌终端),零模拟层
- 依赖 harness resume(P0/P1 验证 `--resume` / claw 重连行为)
- 简化部署:无 ttyd/ws 中间层,TUI 直接 spawn

---

## ADR-8: 持久化 = harness resume(无 tmux/screen;claude --resume / claw session key)

**Context**:tmux/screen 原管"断线不丢"持久化。但 claude code `--resume` 和 claw session key 已解决重连恢复 — harness 原生持久化层已存在,再加 tmux 是重复造轮子 + 多一层会话管理复杂度。

**Decision**:**持久化交 harness**(claude `--resume <sid>` / claw session key 重连同 session)。**不加 tmux/screen**。PTY 死 → 重 spawn `claude --resume <sid>` 接回(新进程,旧 session context)。**例外**:某 harness 既无 resume 又要 raw PTY 保活才加 tmux(目前 claude/claw 都有 resume,不触发)。

**Consequences**:
- PTY 死 → 重 spawn `claude --resume <sid>` 接回(新进程,旧 session context)
- 省 tmux/screen 复杂度(会话管理 / pane / 复用层)
- 风险:若 harness resume 不完整(丢 context),需回头加 tmux 兜底 — 实现时验证 `claude --resume` / claw 重连行为
- 例外条款留口子:未来接入无 resume 的 harness 时不阻塞

---

## ADR-9: TUI 形态(单 binary 跨平台 + 本地优先 + 远程 SSH + 无 CORS / 无 web 栈 / transport 抽象作废)

**Context**:定 ratatui(ADR-6)后,需明确 TUI 形态 / 部署 / 跨域 / 传输层抽象。旧 ADR 的 transport 抽象(web ws + 未来 Tauri IPC)是为浏览器 + 桌面壳双栈设计 — TUI 既不经浏览器,该抽象无存在必要,留着是死代码。

**Decision**:
- (a) **单 binary 跨平台**:`cargo build --release` 出 `v2-tui`,用户机器零依赖直接跑(任何终端)
- (b) **本地优先**:TUI + orchestrator/observe 同机(REST/WS 连本地)。远程后续 SSH(P2+)
- (c) **无 CORS**:TUI 无浏览器;orchestrator 回控 claw/claude 是服务端→服务端(Python WS/subprocess,不执行浏览器同源策略)。浏览器侧无跨域(TUI 不经浏览器)
- (d) **transport 抽象作废**:旧 ADR 的 PTY transport interface(web ws / Tauri IPC 双实现)随 web 栈一并废除 — 无浏览器传输层,无需可替换抽象

**Consequences**:
- 无 web 栈 / CORS / transport 抽象 — 旧 ADR transport interface **作废**,不留残文
- 部署最简(单 binary + 后端起)
- 远程需 SSH(P2+,TUI SSH 到后端机跑),非浏览器远程
- 依赖 harness resume 实现(验证 claude `--resume` / claw 重连,见 ADR-8)
- 服务端→服务端不受同源策略约束,orchestrator 回控路径无跨域配置负担

---

## 决策一致性矩阵(9 决策 × 影响 × 阶段)

| 决策 | 主题 | 影响 | 阶段 |
|------|------|------|------|
| ADR-1 | 薄原语层(重编排不废弃) | orchestrator 重定位;memory REST 独立数据服务 | P0(原语)/ P2+(编排升级) |
| ADR-2 | observe 纯观测(/send 移除 + gateway send 搬 orche) | observe 拆 send;observe 只收 | P0 |
| ADR-3 | harness-level bridge 定位(非 chat) | 定位差异化;原语 harness 语义 | 全局 |
| ADR-4 | orchestrator 唯一 harness 客户端(observe 不连 harness) | 单一事件源;消除抢事件;事件流单向 orche→observe | P0 |
| ADR-5 | 多 session/多实例并行无锁(active=焦点非锁) | 并发模型;active/观测解耦 | P0(基础)/ P2(深耕) |
| ADR-6 | 前端 ratatui TUI(Rust;布局化非节点图) | 前端 rebuild;推翻 web canvas.live/observe | P1 |
| ADR-7 | raw = TUI exec 子进程(甩 web 终端栈) | raw 通路;免 xterm.js/ws/node-pty/ttyd/tmux | P2 |
| ADR-8 | 持久化 = harness resume(无 tmux/screen) | 免 tmux;依赖 --resume/claw session key | P1(验证)/ P2(兜底) |
| ADR-9 | TUI 形态(单 binary + 本地优先 + SSH + 无 CORS/transport 作废) | 部署;跨域;废除旧 transport 抽象 | 全局(P0 后端)/ P2+(SSH) |

### 分阶段路线(对应矩阵)

- **P0**:后端原语层 + observe 纯化(curl 验证,不依赖前端)— ADR-1/2/3/4/5 + ADR-9 后端半
- **P1**:ratatui TUI(flow/stack/popup demo + 接 orchestrator 控制)— ADR-6 + ADR-8 验证
- **P2**:raw exec + 多实例深耕 + 编排原语升级(turn 链/分支/DAG)— ADR-7 + ADR-5 深耕 + ADR-1 升级
- **P3+**:Tauri 桌面壳 / memory 独立数据服务 / 远程 SSH

---

> 关联 spec:[[harness-bridge-orchestrator]](../specs/harness-bridge-orchestrator.md)
