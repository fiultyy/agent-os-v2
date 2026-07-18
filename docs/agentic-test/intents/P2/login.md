> ⚠️ **历史快照(gateway 已退役,2026-07-18)**:本文档含 `:8000` curl 引用指向已删除的 gateway BFF(commit c594969)。现役服务:orchestrator `:8001` / observe `:8002` / native `/h`。文中 `:8000` 示例为失效死引用,不再维护。

---
name: login
target: http://localhost:8000
tags: [smoke, edge, auth, api]
timeout_ms: 60000
default_skip: true
requires: [AUTH_ENABLED=true, AUTH_API_KEYS]
status: ready
---

> IT-4 通电(api step)+ 配置依赖:需 AUTH_ENABLED=true + AUTH_API_KEYS(compose 默认 false → /auth/token 恒 401)。
> 纯 api POST /auth/token 验证 JWT 闭环(auth.py:101-126):有效 key 换 access/refresh token;无效 key 401。
> 临时配:compose gateway 加 AUTH_ENABLED=true + AUTH_API_KEYS=qa-test-key-1,up -d gateway;跑完恢复 false(避免破坏其他 intent 无认证访问)。

# JWT 登录鉴权闭环

## 目标
验证 API key 登录获取 JWT(access + refresh token),无效 key 被 401 拒绝。

## 前置
- gateway AUTH_ENABLED=true + AUTH_API_KEYS 含 "qa-test-key-1"
- 临时配(compose env),跑完恢复 false

## 步骤
1. (api) POST /auth/token headers: {"Content-Type":"application/json"} body: {"api_key":"qa-test-key-1"} ||| 有效 key 换 JWT
2. (api) POST /auth/token headers: {"Content-Type":"application/json"} body: {"api_key":"invalid-key"} ||| 无效 key 拒绝

## 权威信号
- [step 1] POST /auth/token 有效 key 返回 200 + JSON 含 access_token(非空字符串)
- [step 1] 响应含 refresh_token(非空) + token_type="bearer" + expires_in(正整数)
- [step 2] 无效 key 返回 401(鉴权拒绝,auth.py:111 not in AUTH_API_KEYS)
