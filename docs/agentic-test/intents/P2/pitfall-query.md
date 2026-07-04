---
name: pitfall-query
target: http://localhost:8001/v1/pitfall/
tags: [smoke, edge, api]
timeout_ms: 60000
status: NOT-WIRED
---

> NOT-WIRED: signal 全断言 API 响应(GET /v1/pitfall/ /search /match 字段、recurrence_count、空列表、disabled),浏览器 stagehand 看不到网络层 → 全 false。纯 API 契约测试应 curl 直测,非浏览器 intent。

# PitFail 踩坑查询

> ⚠️ gateway 无 pitfall proxy(main.py 无 router),前端 :3000 /v1/pitfall 不在 rewrite 规则 → 前端 404,应直连 orchestrator:8001。orchestrator FastAPI `redirect_slashes=False`(engine.py:48),GET /v1/pitfall(无末尾 /)→ 404(pitfail.py:42 是 `@router.get("/pitfall/")`,router 挂 /v1),主端点必须末尾 /。

## 目标
验证 PitFail 踩坑库查询通路可用,工具执行失败时记录的踩坑可被检索(对应 P0 defer 通电项)。

## 前置
- 应用已启动,有工具执行失败过(chat.py 工具失败分支已 record pitfall);无失败则返回空列表

## 步骤
1. (extract) 调用 `GET /v1/pitfall/`,抽取返回的踩坑条目列表结构(每条含 id / file_path / error_type / symptom / root_cause / fix / recurrence_count / tags),且按 newest first 排序
2. (extract) 调用 `GET /v1/pitfall/search?query=<关键词>`,抽取 LIKE 搜索结果
3. (extract) 调用 `GET /v1/pitfall/match?file_path=<路径>&error_type=<类型>`,抽取精确匹配结果
4. (observe) 检查空场景:无工具失败时 `GET /v1/pitfall/` 返回空列表 `[]`
5. (observe) 检查降级场景:pitfall_registry 未初始化时返回 `{"disabled": true, "items": []}`

## 权威信号
- `GET /v1/pitfall/` 返回数组,每条条目含 file_path / error_type / symptom / root_cause / fix / recurrence_count / tags 字段
- 条目按 newest first 排序(最新失败在前)
- `/v1/pitfall/search?query=X` 对关键词做模糊匹配,返回命中条目
- `/v1/pitfall/match?file_path=&error_type=` 做精确匹配,返回命中条目
- 同一踩坑重复命中时 recurrence_count 递增
- 无工具失败时返回空列表 `[]`
- registry 未初始化时返回 `{"disabled": true, "items": []}` 而非报错
- 主端点末尾必须有 /(redirect_slashes=False,无 / 则 404)
