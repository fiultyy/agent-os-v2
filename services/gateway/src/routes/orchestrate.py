"""Orchestrate route — SSE proxy to orchestrator /orchestrate.

代理前端 POST /orchestrate → orchestrator:8001/v1/orchestrate,SSE 流式透传
(multi_agent / fan_in / synthesizer / execution_complete 事件)。
模板抄 routes/execute.py;timeout 300s(多 agent 编排比单轮 execute 久)。

错误透传修复(同 execute.py):上游对无效 orchestrator_agent_id / 空
sub_agents 返 404/400 JSONResponse(orchestrate.py:91-99)。原实现把
``raise_for_status()`` 放进 async generator,StreamingResponse 默认 200
已在 generator 首次 yield 前 commit → 上游 4xx 只能在 200 流里中断,
前端拿「200 + 空流」空等到超时。修法:stream 上下文内先拿 status_code
(headers 阶段即可用,不消费 body)→ 4xx/5xx 透传状态码 + 错误体,2xx 走 SSE。
只发一次上游请求(编排有 agent 成本)。
"""
import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from src.config import ORCHESTRATOR_API
from src.config import http_client

router = APIRouter()


@router.post("")
async def orchestrate(request: Request):
    """Proxy orchestrate request to orchestrator, streaming SSE events back."""
    body = await request.json()

    try:
        resp = await http_client.send(
            http_client.build_request(
                "POST",
                f"{ORCHESTRATOR_API}/orchestrate",
                json=body,
                timeout=300.0,
            ),
            stream=True,
        )
    except httpx.RequestError:
        return JSONResponse(
            {"error": "orchestrator unreachable", "detail": "request error"},
            status_code=502,
        )

    if resp.status_code >= 400:
        try:
            await resp.aread()
            err_body = resp.json()
        except Exception:
            err_body = {"error": resp.text}
        finally:
            await resp.aclose()
        return JSONResponse(err_body, status_code=resp.status_code)

    async def stream_events():
        try:
            async for line in resp.aiter_lines():
                yield line + "\n"
        finally:
            await resp.aclose()

    return StreamingResponse(
        stream_events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
