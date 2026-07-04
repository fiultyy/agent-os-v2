---
name: pitfall-query
target: http://localhost:8001
tags: [smoke, edge, api]
timeout_ms: 60000
status: ready
---

> IT-4 通电(v0.5 api step)+ IT-4.2(92385ae checker step-scoped):纯 API 契约测试,runtime 懒起 runner(不起 Chrome),直 fetch orchestrator :8001。
> gateway 无 pitfall proxy,故 target=orchestrator 直连。默认不带认证(SERVICE_AUTH_KEY 未配 → 放行)。
> 恢复边界 step(无/404 + no-such-file 走 match 路由返 200 not found)验 IT-4.2 step-scoped:checker 判 step3 match 信号只看 step3 evidence(200+[]),不被 step4 的 404 污染。

# PitFail 踩坑查询

## 目标
验证 PitFail 踩坑库查询通路可用 + 边界行为(无/404、不存在的 file_path 走 match 路由)。

## 前置
- orchestrator :8001 健康
- 有工具执行失败过则返回踩坑条目;无失败则空列表(空场景仍断言结构)

## 步骤
1. (api) GET /v1/pitfall/ ||| 抽取踩坑条目列表(数组结构)
2. (api) GET /v1/pitfall/search?query=工具 ||| LIKE 模糊搜索结果
3. (api) GET /v1/pitfall/match?file_path=/tmp/x&error_type=ValueError ||| 精确匹配结果
4. (api) GET /v1/pitfall ||| 无末尾 / 触发 404(redirect_slashes=False)
5. (api) GET /v1/pitfall/no-such-file ||| 不存在的 file_path 走 match 路由返 200 not found

## 权威信号
- `GET /v1/pitfall/` 返回 200 + JSON 数组(空列表或踩坑条目)
- `GET /v1/pitfall/search?query=X` 返回 200 + JSON 数组(空或命中条目,LIKE 模糊匹配)
- `GET /v1/pitfall/match?file_path=/tmp/x&error_type=ValueError` 返回 200 + JSON 数组(空或精确命中,参数非空走 match 路由)
- `GET /v1/pitfall`(无末尾 /)返回 404(redirect_slashes=False,无 / 则 404)
- `GET /v1/pitfall/no-such-file` 返回 200 + not found 错误体(match 路由把 no-such-file 当 file_path 参,找不到返 not found;非 404)
- 无工具失败时主端点返回空列表 `[]`(非报错)
- 若返回非空条目,每条含 file_path/error_type/symptom/root_cause/fix/recurrence_count/tags 字段(空列表时此信号视为满足)
