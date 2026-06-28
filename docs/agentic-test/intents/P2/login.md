---
id: login
title: JWT 登录(仅 gateway 部署)
page: /login
api: POST /auth/token
priority: P2
---

# 意图
用户通过 API key 登录,获取 JWT(access + refresh token),后续请求带 Bearer 认证。验证 JWT 鉴权闭环。

# 前置
- **gateway 部署 + AUTH_ENABLED=true**(单容器无 gateway → 此 intent 不能跑,见失败模式)
- AUTH_API_KEYS 配置了有效 API key

# 步骤
1. 打开 /login 页面
2. 输入 API key
3. 提交登录

# 验证
- UI:登录成功跳转 /;失败显示错误信息
- API:`POST /auth/token` 返回 `{access_token, refresh_token, token_type, expires_in}`
- token 存 localStorage(`agent_os_access_token`),后续请求带 `Authorization: Bearer`

# 失败模式
- **单容器部署(无 gateway)**:orchestrator 8000 无 `/auth` 路由 → login intent **不能跑**(AUTH_ENABLED 默认 false,无需认证)
- API key 无效 → 401
- access token 15 分钟过期 → refresh token(7 天)刷新
