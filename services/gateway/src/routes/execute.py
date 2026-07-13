"""Execute route — SSE proxy to orchestrator /execute."""
import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from src.config import ORCHESTRATOR_API
from src.config import http_client

router = APIRouter()


@router.post("")
async def execute(request: Request):
    """Proxy execute request to orchestrator, streaming SSE events back.

    错误透传修复(bug:gateway SSE 代理吞 4xx → 「200 + 空流」):
    上游 orchestrator 对无效 agent_id 返回 4xx(chat.py:867-868 → 404
    JSONResponse)。原实现把 ``raise_for_status()`` 放进 async generator,
    而 ``StreamingResponse`` 默认 status_code=200 已在 generator 首次 yield
    前 commit 给客户端 → 上游异常只能在已发送的 200 流里中断,前端拿不到
    error,空等到 SSE 超时。

    修法:用 ``http_client.send(..., stream=True)`` 打开上游响应,
    **在消费任何 body 字节之前** ``resp.status_code`` 就已可用(httpx 在
    HEADERS 阶段解析)。据此分支:
      • 上游 4xx/5xx → 读上游错误体(orchestrator 返 JSONResponse,体小)
        → 以**透传状态码** + 原 JSON 回前端(前端 API client 据状态码识别
        error,不再空等超时)。
      • 上游 2xx    → 正常 SSE 流式透传。

    只发**一次**上游请求(避免重复执行 agent / 双倍 token 成本)。
    """
    body = await request.json()

    # ── Field mapping: accept "message" as "input" ───────────
    # Frontend/curl may send {"message": "..."} while the
    # orchestrator expects {"input": "..."}.  Normalise here
    # (BFF responsibility) so both field names work.
    if "message" in body and "input" not in body:
        body["input"] = body["message"]

    # 单次上游请求;在 stream 上下文内拿到 status_code 后再决定如何回。
    # 不 raise、不重复请求(agent 执行有副作用 + 成本)。
    try:
        resp = await http_client.send(
            http_client.build_request(
                "POST",
                f"{ORCHESTRATOR_API}/execute",
                json=body,
                timeout=120.0,
            ),
            stream=True,
        )
    except httpx.RequestError:
        return JSONResponse(
            {"error": "orchestrator unreachable", "detail": "request error"},
            status_code=502,
        )

    if resp.status_code >= 400:
        # 透传上游 4xx/5xx:读完整错误体(orchestrator 返 JSONResponse,体小)
        # → 原样回前端 + 透传状态码。前端 API client 据状态码识别 error。
        try:
            await resp.aread()
            err_body = resp.json()
        except Exception:
            err_body = {"error": resp.text}
        finally:
            await resp.aclose()
        return JSONResponse(err_body, status_code=resp.status_code)

    # 上游 2xx → SSE 流式透传(沿用原行为,主路径零回归)。
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
