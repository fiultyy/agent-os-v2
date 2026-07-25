# 轴 1:工具层清理 — 删死代码 + ToolLayer 决策(非四层重构)

Date: 2026-07-25(第三轮读代码后回退重写)
Status: Design(轻清理,待 plan)
依赖:无

**判断结论(2026-07-25 读代码后)**:当前工具层**主体合理**(capability 组件化 / a2a mesh / ToolExecutor+guardrail / memory 子系统均清晰),问题是**冗余非结构缺陷**。曾设计的"基建层 kernel / tools / binding / skill 四层正交重构"基于"ToolLayer 混维度伤害运行时"的前提,**读代码证伪**(见 §6 回退记录)→ **回退,降级为轻清理**。

关联:轴 2(工具调用 fire PreToolUse/PostToolUse)

## 1. 现状(有证据)

### 1.1 合理的部分(不动)
- `src/harness/capabilities/` 8 capability(profile/memory/guardrail/observe/skill/tool_bridge/cwd_scope/engineering_discipline)— pydantic-ai 原生,职责清晰
- `src/a2a/` transport/card/tool — mesh 清晰
- `src/tools/executor.py` + `guardrail.py` — 执行 + 防护分离
- `src/skills/` skill_loader/skill_executor — 发现/加载机制(发现机制雏形)

### 1.2 冗余/死代码(真问题)
1. **ToolLayer 三档是死元数据**:`ToolLayer(PRIMITIVE/SKILL/COMPOSITE)`(`catalog.py:15`)注册时写,但 `list_by_layer`/`filter_dict`/`list_by_category`/`list_by_tags` **零调用方**(grep 全空)→ catalog 设计了没人用
2. **死包**:`src/skill_catalog/`(0 py,纯 `__pycache__`)/ `src/control/`(空占位,`__init__.py` 自承 "safe to delete")
3. **命名/归属混乱**:`primitive` 在 `src/skills/primitive/`、`composite` 在 `src/tools/composite/`、`src/skill_catalog/` 空、又有 `src/skills/` 池 + 外部 `services/skills/` — primitive/composite/skill 三名字散落
4. **ToolRegistry 双写**(`registry.py`):`_tools` dict + `ToolCatalog` 两份,catalog 没人读
5. **code_* 是 SKILL 但本质 IO**:code_read/write/search 与 file_* 同类(文件操作),却分 SKILL/PRIMITIVE 两层 — 语义错位(但因 layer 不驱动运行时,无害)

## 2. 清理项(轻量,非重构)

### C1 删死包(必做,零风险)
- 删 `src/skill_catalog/`(0 py)
- 删 `src/control/`(`__init__.py` 已标 safe to delete)

### C2 ToolLayer 决策(三选一)
layer 字段零运行时消费,三选一:
- **(a) 删 catalog 整套**(推荐,ponytail):ToolLayer 枚举 + ToolCatalog + ToolCatalogEntry + ToolCatalogAPI + register 的 layer 参数全删。ToolRegistry 只留 handler map。
- **(b) 真用它分流**:ToolBridgeCapability 按 layer 过滤工具(如只暴露 PRIMITIVE 给低权限 agent)— 当前无此需求,YAGNI
- **(c) 保留元数据但删 SKILL 档**:code_* 归 PRIMITIVE(与 file_* 同类),留 PRIMITIVE/COMPOSITE 两档作分类标签

推荐 **(a)** — catalog 零消费,删最干净;未来真要分类再加。

### C3 命名归一(可选,defer)
primitive/composite/skill 三名字散落三处。理想:io 工具实现归 `src/tools/`,skill 只放 fusion 声明。但搬动改 import 路径(回归面),**defer 到值得时**(如真要做 skill fusion 外部 CLI 集成)。

### C4 ToolRegistry 单写(随 C2)
C2(a) 删 catalog 后,ToolRegistry 自然单写(只 _tools dict)。

### C5 skill = 发现机制 + fusion(保留洞察,defer 实施)
skill 不当工具档,当声明(fusion:外部 CLI + 脚本 + 三方 + io 原语)。现有 SkillCapability defer load 已是发现机制雏形。**fusion schema(external_cli/scripts/primitives/strategy)defer 到真有外部 CLI 集成需求**。

## 3. 实施路径(1-2 轮)
- **P0 删死代码**:C1(删 skill_catalog/control)+ C2(a)(删 catalog 整套)+ C4。单测保 ToolExecutor 行为不变。
- **P1(可选/defer)**:C3 命名归一 / C5 skill fusion schema — 等需求驱动

## 4. 验收
- [ ] `src/skill_catalog/` + `src/control/` 删除,grep 零残留 import
- [ ] ToolLayer/ToolCatalog 删除(C2a),ToolRegistry 单写
- [ ] ToolExecutor + 现有 18 工具(primitive 15 + skill 3)+ workflow/a2a/create_agent 行为不变(回归)
- [ ] 全套件 pytest 不退步

## 5. 风险 / defer
- C2 删 catalog:确认无外部 API 暴露 catalog(`filter_dict` 等)— grep 确认零路由用,删
- C3 命名归一:import 路径改动回归面大,defer
- C5 skill fusion:等外部 CLI 集成需求

## 6. 回退记录(留痕)
**曾设计(2026-07-25 第一/二轮 grill)**:轴 1 "基建层 kernel runtime / tools 层 / binding / skill 四层正交重构",把 io/agent/orchestrate/a2a 抽成原语方法层 + 拆 kernel/tools + binding 横切 + skill fusion 声明,配套 ToolLayer 大爆炸废弃 + agents.yaml 破坏性迁移 + 原语细粒度。

**证伪(第三轮读代码)**:
- ToolLayer 零运行时消费(catalog `list_by_*`/`filter_dict` 全空调用)→ "混维度伤害运行时"前提不成立
- 基建层散落非重复:agent/turn/session 通过 `_state` 单例 + event bus 已解耦,turn 是 bus+多消费者(非重复实现)
- capability 层已存在且清晰,不需重设计

**结论**:四层重构是 over-engineering(为重构而重构,ponytail 禁)。降级为 C1-C5 轻清理。保留的有价值洞察:skill = 发现机制 + fusion(C5,defer 实施)。
