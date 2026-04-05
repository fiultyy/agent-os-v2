"""Execute route — SSE proxy to orchestrator /execute."""
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from src.config import ORCHESTRATOR_URL
from src.config import http_client

router = APIRouter()


@router.post("/")
async def execute(request: Request) -> StreamingResponse:
    """Proxy execute request to orchestrator, streaming SSE events back."""
    body = await request.json()

    async def stream_events():
        async with http_client.stream(
            "POST",
            f"{ORCHESTRATOR_URL}/execute",
            json=body,
            timeout=120.0,
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
