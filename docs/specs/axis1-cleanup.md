# Spec: axis1-cleanup
Date: 2026-07-25
Status: Draft → 待 go Locked
Base: 8f38f89

## 1. Problem
AO2 工具层有死代码冗余:`src/tools/catalog.py`(ToolLayer/Catalog/Entry/API)零运行时消费(grep `list_by_layer`/`filter_dict` 等全空调用),`src/skill_catalog/`(0 py)+ `src/control/`(空占位)是残留死包。ToolRegistry 双写(`_tools` + catalog)是负担。

## 2. Solution
删 catalog 整套(ADR-1)+ 删空包(ADR-2)。ToolRegistry 单写。18 工具/workflow/a2a/create_agent 行为不变。

## 3. Out-of-Scope(defer)
- C3 命名归一(primitive/composite/skill 三名字散落,import 路径回归面大)
- C5 skill fusion schema(等外部 CLI 集成需求)
- 轴 2 hooks 补全 / 轴 3 ACP(独立阶段)

## 4. User Stories
- 作为开发者,我翻 src/tools/ 不再见死元数据 catalog,ToolRegistry 单一职责
- 作为开发者,src/ 目录无空包噪音(skill_catalog/control)

## 5. Implementation Decisions
- [ADR-1] 删 catalog 整套(C2a)
- [ADR-2] 删空包(C1)

## 6. Testing Decisions(seams)
- qa_available=false(无 web QA)→ verify 走 general_test(grep 零残留 + pytest 回归)+ skeptic
- 回归基线:pytest 全套件 vs base 8f38f89(839 passed,cd-cleanup memory;fix-e2e-bugs 有 8 预存 fail=_sessions/_store 模块级污染,单独跑 pass)
- skeptic:死代码确认 + ToolExecutor 行为不变 + 红线(R1/R5/RK11)未碰

## 7. Acceptance(节点映射)
- 节点 A.T1(删空包)→ ADR-2:grep 零 import + 两目录删
- 节点 A.T2(删 catalog 链)→ ADR-1:grep 零 catalog 残留 + ToolRegistry 单写 + pytest 不退步 + 红线守

## 8. Open Issues
(无,grill 收敛)

## 9. Defer 预判
- C3 命名归一(import 路径回归面,等 skill fusion 需求时一起)
- C5 skill fusion schema(external_cli/scripts/primitives/strategy)
- ToolLayer 未来若要分类工具重新加(YAGNI 现在不加)
