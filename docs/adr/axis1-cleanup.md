# ADR: axis1-cleanup
Date: 2026-07-25
Status: Active
Base: 8f38f89

## ADR-1: 删 catalog 整套(C2a)
Status: Accepted
Date: 2026-07-25
Context: ToolLayer(PRIMITIVE/SKILL/COMPOSITE)+ToolCatalog+ToolCatalogEntry+ToolCatalogAPI(catalog.py)是 L3.4 设计的统一工具目录,意图按 layer/category/tag 索引工具。读代码证伪:`list_by_layer`/`filter_dict`/`list_by_category`/`list_by_tags` grep 全零调用方,catalog 是死元数据,layer 不驱动任何运行时决策(ToolBridge/ToolExecutor 不按 layer 分流)。code_*(SKILL)与 file_*(PRIMITIVE)同是文件操作却分两层,语义错位(因 layer 不驱动而无害)。维持=双写负担(`_tools` dict + catalog 两份)+ 认知噪音。详见 docs/design/axis-1-tool-relayer.md。
Decision: 删 catalog 整套 —— ToolLayer 枚举 + ToolCatalog + ToolCatalogEntry + ToolCatalogAPI(catalog.py 整文件)+ ToolRegistry 的 catalog 字段/get_catalog + register 的 layer 参数。engine.py 22 处 ToolLayer.XX + _bulk_register 的 layer 传参清理,工具注册清单 tuple 从 5 元素降 4 元素。ToolRegistry 单写(只 _tools dict)。
Alternatives:
- (c) 留 PRIMITIVE/COMPOSITE 两档删 SKILL:catalog 仍死代码(没人 list_by_layer),治标不治本
- (b) 真用 layer 分流(ToolBridge 过滤):当前无需求,YAGNI
Consequences: ToolRegistry.register 签名变(去 layer 参数,破坏性,公开 API);engine.py 工具注册清单 tuple 5→4 元素。未来要分类工具重新加(轻量)。ToolExecutor/18 工具/workflow/a2a/create_agent 行为不变(回归保证)。
Constrains: [T2]

## ADR-2: 删空包(C1)
Status: Accepted
Date: 2026-07-25
Context: `src/skill_catalog/`(0 py,纯 `__pycache__`)+ `src/control/`(仅空 `__init__.py` 占位,自承 "safe to delete")是历史重构残留死包,增目录噪音。
Decision: `rm -rf src/skill_catalog/ + src/control/`。grep 确认零 import 引用后删。
Alternatives: 保留(无代价但噪音)。
Consequences: 目录更干净。零行为影响(无代码引用)。
Constrains: [T1]
