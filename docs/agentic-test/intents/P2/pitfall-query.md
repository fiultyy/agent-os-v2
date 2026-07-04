---
name: pitfall-query
target: http://localhost:8001
tags: [smoke, edge, api]
timeout_ms: 60000
status: ready
---

> IT-4 通电(v0.5 api step):纯 API 契约测试,runtime **懒起 runner**(不起 Chrome),直 fetch orchestrator :8001。
> gateway 无 pitfall proxy(前端 :3000 /v1/pitfall 不在 rewrite 规则),故 target=orchestrator 直连。
> 默认不带认证(SERVICE_AUTH_KEY 未配 → 放行);api step ok=收到响应,status 断言由权威信号判。
> 主 intent 聚焦查询通路(GET/search/match 返 200+数组);边界行为(无/触发 404、no-such-file 走 match 路由返 200+not found)已实测确认,见 ## 注 段,不进 step(避免 checker evidence pool 把 404 混入 match 信号判定)。

# PitFail 踩坑查询

## 目标
验证 PitFail 踩坑库查询通路可用,工具执行失败时记录的踩坑可被检索(对应 P0 defer 通电项)。

## 前置
- orchestrator :8001 健康
- 有工具执行失败过(chat.py 工具失败分支已 record pitfall);无失败则返回空列表(空场景仍断言结构)

## 步骤
1. (api) GET /v1/pitfall/ ||| 抽取踩坑条目列表(数组结构)
2. (api) GET /v1/pitfall/search?query=工具 ||| LIKE 模糊搜索结果
3. (api) GET /v1/pitfall/match?file_path=/tmp/x&error_type=ValueError ||| 精确匹配结果

## 权威信号
- `GET /v1/pitfall/` 返回 200 + JSON 数组(空列表或踩坑条目)
- `GET /v1/pitfall/search?query=X` 返回 200 + JSON 数组(空或命中条目,LIKE 模糊匹配)
- `GET /v1/pitfall/match?file_path=&error_type=` 返回 200 + JSON 数组(空或精确命中)
- 无工具失败时主端点返回空列表 `[]`(非报错)
- 若返回非空条目,每条含 file_path/error_type/symptom/root_cause/fix/recurrence_count/tags 字段(空列表时此信号视为满足)

## 注(边界行为,实测确认,不进 step 避免 pool 污染)
> - `GET /v1/pitfall`(无末尾 /)→ 404(orchestrator FastAPI `redirect_slashes=False`,engine.py:48)
> - `GET /v1/pitfall/no-such-file` → 200 + `{"error":"not found","id":"no-such-file"}`(match 路由把 no-such-file 当 file_path 参,找不到返 not found;非 404)
> - 这两个边界 step 的 evidence(404 / not found)若进 pool 会混入 match 信号判定 → checker 误判 false(已 5/7 实证)。故边界仅文档记录,不跑。
