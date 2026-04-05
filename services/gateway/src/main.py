"""FastAPI application entry point."""

import os

from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware

from src.auth import init_keys
from src.config import AUTH_ENABLED, http_client
from src.middleware import require_auth, require_service_key
from src.routes import agents, prompts, conversations, resources, memories, messages, debug, kg, chat, execute
from src.routes import auth as auth_routes

app = FastAPI(title="Agent OS — API Gateway", version="0.1.0", redirect_slashes=True)


@app.on_event("startup")
async def _startup() -> None:
    """Initialise auth keys on startup."""
    init_keys()


@app.on_event("shutdown")
async def _shutdown() -> None:
    await http_client.aclose()

# CORS: allow_origins=["*"] with allow_credentials=True is rejected by browsers.
# Use explicit origins from env, or allow all without credentials.
_cors_origins = os.environ.get("CORS_ORIGINS", "").split(",")
_cors_origins = [o.strip() for o in _cors_origins if o.strip()]

if _cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
else:
    # Development fallback: allow all origins but without credentials
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

# ── Auth routes (always mounted, rate-limited) ────────────────────────────────
app.include_router(auth_routes.router, prefix="/auth", tags=["auth"])

# ── Application routes (protected when AUTH_ENABLED=true) ─────────────────────
app.include_router(
    agents.router,
    prefix="/agents",
    tags=["agents"],
    dependencies=[Depends(require_auth)],
)
app.include_router(
    prompts.router,
    prefix="/prompts",
    tags=["prompts"],
    dependencies=[Depends(require_auth)],
)
app.include_router(
    conversations.router,
    prefix="/conversations",
    tags=["conversations"],
    dependencies=[Depends(require_auth)],
)
app.include_router(
    resources.router,
    prefix="/resources",
    tags=["resources"],
    dependencies=[Depends(require_auth)],
)
app.include_router(
    memories.router,
    prefix="/memories",
    tags=["memories"],
    dependencies=[Depends(require_auth)],
)
app.include_router(
    messages.router,
    prefix="/messages",
    tags=["messages"],
    dependencies=[Depends(require_auth)],
)
app.include_router(
    debug.router,
    prefix="/debug",
    tags=["debug"],
    dependencies=[Depends(require_auth)],
)
app.include_router(
    kg.router,
    prefix="/kg",
    tags=["kg"],
    dependencies=[Depends(require_auth)],
)
app.include_router(
    chat.router,
    prefix="/chat",
    tags=["chat"],
    dependencies=[Depends(require_auth)],
)
app.include_router(
    execute.router,
    prefix="/execute",
    tags=["execute"],
    dependencies=[Depends(require_auth)],
)


@app.get("/health", dependencies=[Depends(require_service_key)])
async def health() -> dict:
    return {"status": "ok"}
