# SSH 部署:TUI 远程接入 orchestrator/observe

> ADR-9 部署形态:本地优先,远程 SSH(P2+)。TUI 是单 binary,两种远程接入模式。

## 前提:后端机服务

后端机跑(本地直起 or 容器):
```
orchestrator  :8001  (uvicorn src.engine:app,含 /h/* 原语 + /h/flows 编排)
observe       :8002  (uvicorn src.app:app,纯观测)
openclaw      :18789 (node dist/index.js gateway,外部 harness)
LM Studio     :16666 (embedding,memory 用;可选)
```

服务全起后 curl 确认:
```
curl localhost:8001/health  # orche
curl localhost:8002/health  # observe
```

## 模式 A:用户 SSH 到后端机跑 TUI(推荐,ADR-9 本地优先语义)

TUI 在后端机跑,连 localhost(同机,零延迟):

```bash
# 1. SSH 到后端机
ssh user@backend-host

# 2. 拉仓库 + 编译 TUI 单 binary(release,零依赖)
cd ~/projects/agent-os-v2/apps/tui-rs
cargo build --release            # 产出 target/release/v2-tui-rs

# 3. 跑(连 localhost:8001 orche + :8002 observe)
./target/release/v2-tui-rs
```

TUI 连 localhost(后端机)。tab 切 flow/stack/control,f/G/D 创建编排 flow,R 运行,t 触发 turn,e raw exec(spawn claude/claw TUI)。

**优势**:TUI 与 orche/observe 同机,REST/WS localhost 零延迟;raw exec spawn 本地 claude/claw;无 CORS/web 栈。

## 模式 B:本地 TUI + SSH tunnel 远程 orche/observe

TUI 在用户本地机跑,经 SSH tunnel 访问远程后端:

```bash
# 1. 本地起 SSH tunnel(转发 orche + observe 到本地 localhost)
ssh -L 8001:localhost:8001 -L 8002:localhost:8002 user@backend-host

# 2. 本地编译 + 跑 TUI(连 localhost:tunnel → 远程后端)
cd ~/projects/agent-os-v2/apps/tui-rs
cargo build --release
./target/release/v2-tui-rs      # 连 localhost:8001(tunnel)→ 远程 orche
```

**限制**:raw exec(spawn claude/claw TUI)在用户本地机跑 — 若 claude/claw CLI 仅在后端机,raw exec 需模式 A。模式 B 适合纯观测 + 控制(turn/spawn 经或che,raw exec 限本地 harness)。

## 验证

TUI 起后:
- `tab` 切 FLOW/STACK/CONTROL — flow lane 显示 observe 真实 turn 事件
- `c` control mode → `t` 触发 turn → observe 收 tick_started/.../tick_completed
- `f`/`G`/`D` 创建编排 flow(链/分支/DAG)→ `R` 运行 → flow DAG 实时 node 状态
- `?` help(键位 + Kitty 检测)

远程接入无 CORS(TUI 非浏览器;SSH tunnel/同机 localhost 直连)。

## TUI 配置(URL 覆盖)

TUI 默认连 `http://localhost:8001`(orche)+ `http://localhost:8002`(observe)。若后端非 localhost(模式 B tunnel 或自定义),改 `state.rs` 常量 ORCH/OBSERVE 重新 build(或后续加 env / CLI flag 覆盖,P3+)。

## Kitty 终端(图形协议)

TUI 检测终端能力(kitty.rs):Kitty/Alacritty 启 icat 图片(observe 截图直渲)+ 高刷新;非 Kitty 降级(关图/降刷新/单窗口 tab)。SSH 接入若终端不兼容,TUI 自动降级 + 提示。

---

关联:[[ADR-9]](../adr/harness-bridge-orchestrator.md) TUI 形态(单 binary + 本地优先 + 远程 SSH)。
