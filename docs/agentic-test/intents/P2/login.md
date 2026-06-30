---
name: login
target: http://localhost:3000/login
tags: [smoke, edge, auth]
timeout_ms: 60000
default_skip: true
requires: [AUTH_ENABLED=true, AUTH_API_KEYS]
---

# JWT 登录鉴权闭环

> ⚠️ 默认部署(compose AUTH_ENABLED=false)/auth/token 恒 401(AUTH_API_KEYS 空集,任何 key 不匹配);需配 AUTH_ENABLED=true + AUTH_API_KEYS 才可跑。端点 + JWT 流程本身 OK(gateway/src/routes/auth.py:101-126)。

## 目标
验证用户通过 API key 登录获取 JWT(access + refresh token),后续请求带 Bearer 认证。

## 前置
- gateway 已部署且 AUTH_ENABLED=true(单容器无 gateway → 此 intent 不能跑)
- AUTH_API_KEYS 配置了有效 API key

## 步骤
1. (act) 打开 /login 登录页面
2. (act) 在 API key 输入框填写有效 API key
3. (act) 提交登录表单
4. (observe) 查看登录后页面跳转状态
5. (extract) 抽取 POST /auth/token 响应中的 access_token 与 refresh_token

## 权威信号
- 登录成功后页面跳转到首页 / 路径
- 登录成功后页面不再显示登录表单
- 页面通过 API key 无效时显示错误提示(401 相关文案)可见
- POST /auth/token 响应包含 access_token、refresh_token、token_type、expires_in 四个字段
- 浏览器 localStorage 中存在 agent_os_access_token 键(可观测,因 AUTH_ENABLED=false 时中间件不校验,Authorization 头有无不可观测)
