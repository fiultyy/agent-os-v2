---
name: pitfall-query
target: http://localhost:8001
tags: [smoke, edge, api]
timeout_ms: 60000
status: ready
---

> IT-4 通电(v0.5 api step):纯 API 契约测试,runtime **懒起 runner**(不起 Chrome),直 fetch orchestrator :8001。
> gateway 无 pitfall proxy(前端 :3000 /v1/pitfall 不在 rewrite 规则),故 target=orchestrator 直连。
> orchestrator FastAPI `redirect_slashes=False`(engine.py:48),GET /v1/pitfall(无末尾 /)→ 404,主端点必须末尾 /。
> 默认不带认证(SERVICE_AUTH_KEY 未配 → 放行);api step ok=收到响应(2xx/4xx/5xx 均 ok),status 断言由权威信号判。

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
4. (api) GET /v1/pitfall/no-such-subpath ||| 不存在子路径返回 404

## 权威信号
- `GET /v1/pitfall/` 返回 200 + JSON 数组(空列表或踩坑条目)
- 若有踩坑条目,每条含 file_path / error_type / symptom / root_cause / fix / recurrence_count / tags 字段
- `GET /v1/pitfall/search?query=X` 返回 200 + 数组(LIKE 模糊匹配命中条目)
- `GET /v1/pitfall/match?file_path=&error_type=` 返回 200 + 数组(精确匹配条目)
- 主端点末尾必须有 /(redirect_slashes=False,无 / 则 404)
- 不存在的子路径(`/v1/pitfall/no-such-subpath`)返回 404(非 2xx)
- 无工具失败时主端点返回空列表 `[]`(非报错)
