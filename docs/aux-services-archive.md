# 辅助服务归档(prompt-manager / resource-manager)

> **注:conversation-observer 已于 observer 系统双层 evolve(Phase 0)彻底移除**
> ——其 anomaly / error-spike 检测语义迁移至 orchestrator 内向观测层
> `RuntimeObserverHook`(OBSERVER hook,纯内存环形缓冲,不进记忆召回)。
> 本文档剩余内容仅描述 pm/rm 两项的归档状态。

> 状态:**已归档(可逆)** · 决策见 `mvp-acceptance.md:86`(MVP 外 defer 边界)
> 分支:`feat/frontend-observer-iter` · commit:见 `git log`

## 一、归档理由:零消费纯内存孤岛

三项辅助服务在当前 MVP 收敛期是**纯内存孤岛**——没有任何调用方在用它们:

| 维度 | 证据 |
|------|------|
| orchestrator 调用 | 零(grep 无对 8002/8003/8004 的引用) |
| 前端(apps/web)调用 | 零(grep 无对 prompts/conversations/resources 端点的引用) |
| 持久层 | 零(grep 无 `sqlalchemy` / `asyncpg` / `DATABASE_URL`,纯内存状态) |

但 `docker-compose.yml` 的 **gateway `depends_on` 用 `service_healthy` 硬连了它们三个**。
任一辅助服务起不来(健康检查不过),gateway 就无法转 `service_healthy`,进而拖垮
`web → gateway → orchestrator` 这条**主链路**——这是部署鲁棒性短板,与它们"零消费"
的实际地位严重不匹配。

归档 = **把部署耦合解掉,代码全保留、可逆**。采纳"归档"而非"通电":
- **通电**:需要前端面板接线 / A-B prompt 实验 / 模型路由等**真实需求驱动**,
  数天级闭环、当前无反馈源,纯属空转。
- **归档**:解耦部署是小时级、确定性收益,先回收这一层鲁棒性。

## 二、归档做了什么(可逆,主路径零改)

1. **`docker-compose.yml`**
   - 删除 gateway `depends_on` 中 `prompt-manager` / `conversation-observer` /
     `resource-manager` 三条 `service_healthy`(**保留 `orchestrator: service_healthy`**)。
   - 给三个 service 块各加 `profiles: ["aux"]`(**service 块整体保留**,非删除)。
     默认 `docker compose up` **不启动** aux;postgres/web/gateway/orchestrator 不加 profile。
2. **gateway 两路由 502 兜底**
   - `services/gateway/src/routes/{prompts,resources}.py`:
     每个端点的 `http_client` 调用经 `_aux_call` 包装,捕获
     `httpx.HTTPStatusError | httpx.ConnectError | httpx.RequestError`,
     任一失败返回 `502 {"detail":"upstream aux service not wired (archived)"}`。
   - **成功路径逻辑完全保留**(`raise_for_status` + `json()` 行为一致);仅加兜底。
   - 注:`/conversations` 路由及其 `conversations.py` 已在 Phase 0(commit 69179a5
     conversation-observer 彻底弃用)删除,故 aux 502 兜底仅余 pm/rm 两条;
     `main.py` 的 `include_router` 亦无 `/conversations` 注册。
3. **红线未触**:三 service 的 `src/` 与 Dockerfile、orchestrator 全部文件、
   前端 apps/web、gateway 的 `/v1` 转发与 `/health`、记忆模块——**均零改动**。

## 三、恢复方式

### A. 临时起辅助服务(调试/接线验证)

```bash
docker compose --profile aux up
# 或仅起单项:
docker compose --profile aux up prompt-manager
```

带 `--profile aux` 时,三个 aux 服务恢复正常启动;gateway 的 `PROMPT_MANAGER_URL`
等环境变量仍指向它们,端点成功路径自动复用(无需改代码)。

### B. 永久取消归档(回到硬耦合)

从三个 service 块删掉 `profiles: ["aux"]`,并把三条 `service_healthy` 加回 gateway
`depends_on`——即回退本次 commit。本操作完全可逆。

## 四、何时通电(取消归档的前置条件)

通电 = 把"纯内存孤岛"接上**真实反馈闭环**。任一条件成熟即可对该单项通电:

- **prompt-manager(8002)**:前端需要 prompt 模板面板 / A-B prompt 实验位 /
  orchestrator 需要从外部加载而非硬编码 system prompt。
- **resource-manager(8004)**:需要多 provider 动态路由 / 模型别名解析 /
  按成本选模型——且 orchestrator 当前 `default_model` 硬编码已不够用。

在上述真实需求出现前,两项维持归档;`gateway` 两路由的 502 即是其"未通电"的
**正确表征**,不是缺陷。

## 五、与 mvp-acceptance.md defer 边界的呼应

`docs/mvp-acceptance.md:86` 的 MVP 外 defer 清单明确列有
"**辅助服务 observer·rm·pm(归档/通电决策)**"。本次归档是该 defer 项的**结论落地**:
defer 边界保留(不进入 MVP 验收),但用"归档 + 502 兜底 + profiles 隔离"把它的
**部署副作用**从主链路上摘除,使 MVP 的 `web → gateway → orchestrator` 启动
不再被零消费孤岛拖累。通电决策另起迭代,见第四节前置条件。
