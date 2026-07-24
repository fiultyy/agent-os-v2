# 本地启动说明

## 前置
- **LLM key 走 shell env**(非 .env):`ANTHROPIC_AUTH_TOKEN` + `ANTHROPIC_BASE_URL`(orche pydantic-ai AnthropicModel 直读)。`ANTHROPIC_MODEL` 可选(默认 glm-4.7)。
  ```bash
  export ANTHROPIC_AUTH_TOKEN=<智谱 key>
  export ANTHROPIC_BASE_URL=https://open.bigmodel.cn/api/anthropic
  ```
- **miniconda3 sqlite corrupted**:启动脚本自动 `LD_PRELOAD` 系统 libsqlite3(start.sh / Makefile dev-*);或 `start.py` 的 pysqlite3 patch。两者择一。

## 服务组
| 服务 | 端口 | 起法 | 必需? |
|---|---|---|---|
| orchestrator | 8001 | `./start.sh` 或 `make dev-orch` | 是 |
| observe | 8002 | `./start.sh` 或 `make dev-observe` | 是(TUI 观测层)|
| TUI | — | `make dev-tui` 或 `v2-tui-rs` | 是(前端)|
| resource-manager | — | `cd services/resource-manager && python start.py` | 可选(provider config / model routing)|
| prompt-manager | — | `cd services/prompt-manager && ...` | 可选 |

## 一键启动(推荐)
```bash
./start.sh          # orche :8001 + observe :8002(前台,Ctrl+C 停)
# 另开终端:
v2-tui-rs           # 或 make dev-tui;进 TUI 按 4 → Orchestrate tab
```

## 单独起
```bash
make dev-orch       # orche(已含 PYTHONPATH=src + LD_PRELOAD)
make dev-observe    # observe(已含 LD_PRELOAD)
make dev-tui        # TUI(cargo run --release)
```

## 端口约定(勿乱改)
- TUI default `ORCH=http://localhost:8001` / `OBSERVE=http://localhost:8002` —— **orche 必须在 8001**(TUI 硬编码,改端口要同步改 `apps/tui-rs/src/state.rs` 的 `ORCH` 常量)。
- 或che → observe:`OBSERVE_URL=http://localhost:8002`(emit WS)。

## 踩坑(已修)
| 坑 | 现象 | 修法 |
|---|---|---|
| Makefile dev-orch 缺 `PYTHONPATH=src` | `ModuleNotFoundError: a2a`(a2a 是 top-level import)| Makefile dev-orch 加 `PYTHONPATH=src` |
| 缺 `LD_PRELOAD` | miniconda sqlite 崩 | 加 `LD_PRELOAD=/lib/x86_64-linux-gnu/libsqlite3.so.0` |
| start.sh `.env` gate 卡 `LLM_API_KEY` | .env 不存在 → cp 占位 → exit | gate 改 `ANTHROPIC_AUTH_TOKEN`,.env 可选不 cp |
| start.py chdir 写死 `agent-os` | 路径不存在崩 | 改相对 chdir(start.py 所在目录)|
| start.py port 18792 | 与 TUI default 8001 不匹配 → TUI 连不上 | port 改 8001 |
| TUI 软链指主仓 target | worktree 改后跑旧 binary | worktree build 后 `cp` 到主仓 target(或主仓 build)|
| observe events limit cap=1000 | TUI fetch limit>1000 返空 → 拉 events 失败 | fetch limit ≤1000 |

## orche 用啥 env(排查用)
- LLM:`ANTHROPIC_AUTH_TOKEN` / `ANTHROPIC_BASE_URL` / `ANTHROPIC_MODEL`(`native_agent.py` AnthropicModel)
- 其他:`AO2_AGENTS_CONFIG` / `DATABASE_URL` / `PITFALLS_DB` / `CONVERSATIONS_DB` / `MEMORY_*`(见 `engine.py`,均有默认)
- **不读** `.env` 的 `LLM_API_KEY`(旧 schema,仅 gateway/resource-manager 历史用)
