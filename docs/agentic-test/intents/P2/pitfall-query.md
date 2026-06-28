---
id: pitfall-query
title: PitFail 踩坑查询
page: —(API)
api: GET /v1/pitfall
priority: P2
---

# 意图
用户查询工具执行失败时记录的 PitFail(踩坑库),验证 PitFail registry 通电 + 查询通路(P0 defer 通电项)。

# 前置
- 有工具执行失败过(chat.py 工具失败分支 record pitfall);无失败则空

# 步骤
1. (可选)触发一次工具失败(如 fc-tool-call 中工具不存在/文件不存在)
2. `GET /v1/pitfall`(或 `/search?query=` / `/match?file_path=&error_type=`)

# 验证
- API:`GET /v1/pitfall` 返回 `[{id, file_path, error_type, symptom, root_cause, fix, recurrence_count, tags}]`(newest first)
- `/search?query=X`:LIKE 搜索;`/match?file_path=&error_type=`:精确匹配
- 重复失败:recurrence_count 递增(match 命中 increment_recurrence)

# 失败模式
- 无工具失败 → 空列表 `[]`
- pitfall_registry 初始化失败(None)→ `{"disabled": true, "items": []}`
- 错误分类:timeout/file_not_found/permission_denied/tool_error
