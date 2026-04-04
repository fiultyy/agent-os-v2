"""FastAPI application entry point."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.routes import agents, prompts, conversations, resources

app = FastAPI(title="Agent OS — API Gateway", version="0.1.0", redirect_slashes=False)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(agents.router, prefix="/agents", tags=["agents"])
app.include_router(prompts.router, prefix="/prompts", tags=["prompts"])
app.include_router(conversations.router, prefix="/conversations", tags=["conversations"])
app.include_router(resources.router, prefix="/resources", tags=["resources"])


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
