"""Orchestrate route — SSE proxy to orchestrator /orchestrate.

代理前端 POST /orchestrate → orchestrator:8001/v1/orchestrate,SSE 流式透传
(multi_agent / fan_in / synthesizer / execution_complete 事件)。
模板抄 routes/execute.py;timeout 300s(多 agent 编排比单轮 execute 久)。
"""
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from src.config import ORCHESTRATOR_API
from src.config import http_client

router = APIRouter()


@router.post("")
async def orchestrate(request: Request) -> StreamingResponse:
    """Proxy orchestrate request to orchestrator, streaming SSE events back."""
    body = await request.json()

    async def stream_events():
        async with http_client.stream(
            "POST",
            f"{ORCHESTRATOR_API}/orchestrate",
            json=body,
            timeout=300.0,
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                yield line + "\n"

    return StreamingResponse(
        stream_events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
