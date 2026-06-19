# Phase 6 验收文档 — 回滚基线 + 部署 checklist

> 维度 5：rollback-baseline
> 生成时间：2026-06-20
> 当前分支：`feat/memory-docs`(HEAD = `e286d63`)
> 验收范围：P0–P4 memory 迭代(`memory-provenance` → `memory-docs`)

---

## 1. P0–P4 改造总结

### 1.1 分支血缘（已用 `git merge-base --is-ancestor` 校验）

```
master (f29008e)
  └─ feat/memory-provenance (aba43cb)        # P0
       └─ feat/memory-event-bus (58fde6a)    # P1
            └─ feat/memory-cache (3951fee)   # P2
                 └─ feat/memory-state-machine (43d1790)  # P3
                      └─ feat/memory-docs (e286d63)      # P4 ← HEAD
```

线性链验证：provenance 是 event-bus 的祖先，event-bus 是 cache 的祖先，cache 是 state-machine 的祖先，state-machine 是 docs 的祖先。无分叉、无 rebase，可直接整段回退。

### 1.2 每 Phase 一句话 + 代表 commit

| Phase | 一句话总结 | 代表 commit |
|-------|-----------|------------|
| **P0 origin provenance** | 给 `memory_items` 增加 `origin` 列(foreground/agent)，自动巩固(遗忘/反思/迁移/梦境)只作用于 agent 自沉淀项，保护用户手输记忆不被误并 | `86c924a` feat(memory): P0 origin provenance — protect user memories from auto-consolidation |
| **P1 事件总线** | 引入 `MemoryEventBus` + `MemoryHook` 生命周期契约，`DefaultMemoryHook` 封装迁移/压缩/存储；`chat.py` 由直接 memory 调用改为事件分发，`MEMORY_EVENT_BUS_ENABLED=0` 可降级 | `b699892` feat(memory): P1 事件总线 — MemoryEventBus + MemoryHook 生命周期契约<br>`cc015b4` 装配事件总线 + 降级开关<br>`2b03a94` 解耦 chat.py<br>`58fde6a` 单元测试 |
| **P2 三层 Context + cache_control** | context compiler 拆为 static/dynamic/compiled 三层并产出 `CompiledContext`；新增 `prompt_cache` + `llm_client` 双通道 `cache_control`，chat.py 适配 `static_count` | `8bafb6e` feat(context): P2 compiler 三层拆分<br>`475fe17` prompt_cache + llm_client 双通道<br>`ebd911b` chat.py 适配<br>`3951fee` 单测 |
| **P3 确定性状态机** | `MemoryState` 枚举(ACTIVE/STALE/ARCHIVED) + `state`/`last_state_transition` 列 + alembic 0003；`TimeBasedStatePruner` 确定性流转，forgetting 改用 `state` 字段；`TaskConsolidationAgent` 任务后在线巩固 | `e3592fd` MemoryState 枚举<br>`148723a` DB state 列 + alembic 0003<br>`078632d` TimeBasedStatePruner<br>`86443bb` forgetting 用 state 字段<br>`22fbdde` TaskConsolidationAgent<br>`ebdb74c` engine 集成<br>`43d1790` 单测 |
| **P4 文档收尾** | 补 architecture-comparison(主流记忆系统对比 + FAISS 校准 + agentskills.io D-28)与 tech-debt(TD-007/008/009 + roadmap 迭代 10 改造点对齐) | `9f2ec3b` docs: P4 tech-debt TD-007/008/009<br>`e286d63` docs: P4 architecture-comparison |

---

## 2. 回滚基线（原子命令）

所有 hash 取自 `git log --oneline`，已逐 Phase 列出。两条回滚路径：

- **单层回退**（精确撤销某个 Phase，保留其余）：`git revert <hash>..<hash>` 或逐条 `git revert <hash>`
- **整段回退**（一刀回到 Phase 起点）：`git reset --hard <base>`

### 2.1 回退到各 Phase 边界（reset）

| 目标状态 | 命令 |
|---------|------|
| 回退 P4，回到 P3 末尾 | `git reset --hard feat/memory-state-machine` (43d1790) |
| 回退 P4+P3，回到 P2 末尾 | `git reset --hard feat/memory-cache` (3951fee) |
| 回退 P4+P3+P2，回到 P1 末尾 | `git reset --hard feat/memory-event-bus` (58fde6a) |
| 回退 P4+P3+P2+P1，回到 P0 末尾 | `git reset --hard feat/memory-provenance` (aba43cb) |
| 全部回退，回到 master | `git reset --hard master` (f29008e) |

> ⚠️ `git reset --hard` 会丢弃工作区改动。回退前确认无未提交内容。
> ⚠️ P3 含 DB schema 变更(0003 migration)，回退后如要降级 DB，见 §2.3。

### 2.2 单 Phase revert（保留其余 Phase）

```bash
# P4 文档（纯文档，安全）
git revert 9f2ec3b e286d63 --no-edit

# P3 状态机（含 DB 列变更，revert 后需手动处理 schema，见 §2.3）
git revert e3592fd 148723a 078632d 86443bb 22fbdde ebdb74c 43d1790 --no-edit

# P2 三层 Context + cache
git revert 8bafb6e 475fe17 ebd911b 3951fee --no-edit

# P1 事件总线
git revert 923f206 b699892 9369a2d cc015b4 2b03a94 58fde6a --no-edit

# P0 origin provenance
git revert 86c924a aba43cb --no-edit
```

> revert 顺序建议从最新 Phase 往旧 revert（P4 → P0），避免历史依赖冲突。
> revert P3 / P0 会触发 alembic down migration，见下。

### 2.3 DB schema 回滚

P0/P3 通过 `alembic` 引入了 schema 变更，回滚代码须同步回滚 schema：

```bash
# 在 services/orchestrator 下（PostgreSQL / 显式 alembic 路径）
cd services/orchestrator
alembic downgrade 0002   # 回退 P3 state 列（回到只含 origin）
alembic downgrade 0001   # 再回退 P0 origin 列（回到初始 schema）
alembic downgrade base   # 回到完全初始

# SQLiteStore 路径（memories.db）
# SQLiteStore._migrate_schema() 只增不删（ALTER TABLE ADD COLUMN），
# 回退 SQLite 库最稳妥方式是删除列或重建库。若使用 Git tracked 的
# services/orchestrator/data/memories.db，可直接 checkout 旧版本：
git checkout <base> -- services/orchestrator/data/memories.db
```

---

## 3. 部署 checklist

### 3.1 DB migration 顺序

两条路径二选一，**不可混用**：

**路径 A — alembic（PostgreSQL / 显式版本控制）**

```bash
cd services/orchestrator
alembic upgrade head
# 执行序列：0001(initial) → 0002(origin, P0) → 0003(state, P3)
```

migration 文件位置：
- `services/orchestrator/migrations/versions/0001_initial_schema.py`
- `services/orchestrator/migrations/versions/0002_add_memory_origin.py`（down_revision=0001）
- `services/orchestrator/migrations/versions/0003_add_memory_state.py`（down_revision=0002）

**路径 B — SQLiteStore 自动迁移（默认 SQLite 部署）**

`SQLiteStore.__init__` → `_migrate_schema()`（`services/orchestrator/src/memory/sqlitestore.py:116`）在首次连接时自动补列：

```python
# sqlitestore.py:116 _migrate_schema()
# 自动执行（仅 ADD COLUMN，幂等）：
#   ALTER TABLE memories ADD COLUMN origin TEXT DEFAULT 'foreground'        # P0
#   ALTER TABLE memories ADD COLUMN state TEXT DEFAULT 'active'             # P3
#   ALTER TABLE memories ADD COLUMN last_state_transition TEXT DEFAULT ''   # P3
#   UPDATE memories SET state = 'archived' WHERE archived = 1               # P3 回填
```

无需手动执行，应用启动即自动完成。

### 3.2 必需环境变量

| 变量 | 必需 | 默认值 / 说明 | 来源 |
|------|------|--------------|------|
| `LLM_API_KEY` | ✅ | LLM 调用密钥 | agent_manager / llm_client |
| `LLM_BASE_URL` | ✅ | `https://open.bigmodel.cn/api/paas/v4` | resource-manager providers |
| `LLM_MODEL` | ✅ | `glm-4-flash`（`agent_manager.py:60` `os.environ.get("LLM_MODEL", "glm-4-flash")`） | agent_manager |
| `LLM_API_FORMAT` | ✅ | `openai`（`llm_client.py:39` 默认 `openai`；可设 `anthropic`） | llm_client |
| `ANTHROPIC_BASE_URL` | ✅(format=anthropic 时) | `https://open.bigmodel.cn/api/anthropic`（`llm_client.py:45`） | llm_client |
| `ANTHROPIC_AUTH_TOKEN` | ✅(format=anthropic 时) | Anthropic 通道鉴权 token | llm_client |
| `MEMORY_EVENT_BUS_ENABLED` | ⚠️降级开关 | `1`（启用事件总线，默认）；`0` 仅保留 SYSTEM 直写降级模式（`engine.py:93`） | engine |

`.env` 模板示例：

```dotenv
LLM_API_KEY=xxxxxxxxxxxxxxxx
LLM_BASE_URL=https://open.bigmodel.cn/api/paas/v4
LLM_MODEL=glm-4-flash
LLM_API_FORMAT=openai            # 或 anthropic（此时需 ANTHROPIC_BASE_URL/TOKEN）
ANTHROPIC_BASE_URL=https://open.bigmodel.cn/api/anthropic
ANTHROPIC_AUTH_TOKEN=xxxxxxxxxxxxxxxx
MEMORY_EVENT_BUS_ENABLED=1       # P1 事件总线；0=降级（仅 SYSTEM 直写）
```

### 3.3 验证命令

**单元 / 集成测试（orchestrator，Phase 单测齐全）**

```bash
cd services/orchestrator
# P0–P3 关键单测
python -m pytest tests/test_memory_provenance.py \
                 tests/test_memory_event_bus.py \
                 tests/test_memory_default_hook.py \
                 tests/test_compiler_cache.py \
                 tests/test_prompt_cache.py \
                 tests/test_llm_client_channels.py \
                 tests/test_state_pruner.py \
                 tests/test_task_consolidator.py \
                 -v
```

涉及文件（`services/orchestrator/tests/`）：`test_memory_provenance.py`(P0)、`test_memory_event_bus.py`+`test_memory_default_hook.py`(P1)、`test_compiler_cache.py`+`test_prompt_cache.py`+`test_llm_client_channels.py`(P2)、`test_state_pruner.py`+`test_task_consolidator.py`(P3)。

**后端启动 + e2e 验证**

```bash
cd services/orchestrator
# 启动（start.py 内置 pysqlite3 patch，解决 miniconda sqlite3 兼容）
python start.py
# 或
bash run.sh

# e2e 套件
python -m pytest tests/e2e -v
# 计划参照 docs/D-31-e2e-verification.md 与 docs/e2e-verification-plan.md
```

---

## 4. 已知遗留

1. **`memories.db` 被 Git tracked（应解除）**
   `services/orchestrator/data/memories.db`、`kg.db`、`memory.meta.db` 三者被纳入版本库（`git ls-files` 确认），属运行时数据，不应提交。建议：
   ```bash
   git rm --cached services/orchestrator/data/memories.db \
                 services/orchestrator/data/kg.db \
                 services/orchestrator/data/memory.meta.db
   # 并在 .gitignore 中加入 services/orchestrator/data/*.db
   ```

2. **TaskConsolidator extract 需 LLM_MODEL 与 agent model 配对**
   `TaskConsolidationAgent`（P3，`22fbdde`）依赖 LLM 做在线巩固/抽取。其调用模型需与 `agent_manager.py:60` 的 `LLM_MODEL`(默认 `glm-4-flash`)及 agent 自身 model 对齐，否则巩固抽取的格式/语义会与 agent 写入侧不一致，影响 `BackwardWriter origin=AGENT` 的沉淀质量。部署时确认 `LLM_MODEL` 与各 agent 配置的 model 一致。

3. **Phase 6 四优势单测受 miniconda sqlite3 环境限制**
   P0/P3 的 provenance 与状态机单测依赖较新版本的 sqlite3；miniconda 基础环境的 sqlite3 版本过低，需通过 `pysqlite3` 二进制替换 `sqlite3` 模块。`services/orchestrator/start.py` 已内置 patch（`__import__("pysqlite3"); sys.modules["sqlite3"] = pysqlite3`，见 grep 命中），单测如直接 `import sqlite3` 则需同样 patch 或在测试 conftest 注入。运行受此限制的单测前确保 `pysqlite3-binary` 已安装。

---

## 附：关键文件路径

- 回滚基线引用：本文档 §2
- alembic migration：`services/orchestrator/migrations/versions/000{1,2,3}_*.py`
- SQLiteStore 自动迁移：`services/orchestrator/src/memory/sqlitestore.py:116` (`_migrate_schema`)
- 事件总线降级开关：`services/orchestrator/src/engine.py:93`
- LLM 通道默认值：`services/orchestrator/src/services/llm_client.py:39,45`、`services/orchestrator/src/services/agent_manager.py:60`
- 迭代计划：`docs/memory-iteration-plan.md`
- e2e 验证：`docs/D-31-e2e-verification.md`、`docs/e2e-verification-plan.md`
- pysqlite3 patch：`services/orchestrator/start.py`
