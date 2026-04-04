"""Resource Manager — FastAPI entry point.

Endpoints:
- Provider registration and listing
- Model routing and listing
- Load-balanced provider selection
"""

import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel

from src.router import ModelRouter
from src.providers import BaseProvider, OpenAIProvider, AnthropicProvider, ZhipuProvider

app = FastAPI(title="Agent OS — Resource Manager", version="0.1.0", redirect_slashes=False)

_router = ModelRouter()

# ── In-memory stores ─────────────────────────────────────────────

_providers: dict[str, dict[str, Any]] = {}
_models: dict[str, dict[str, Any]] = {}

_builtin_providers: dict[str, BaseProvider] = {
    "openai": OpenAIProvider(),
    "anthropic": AnthropicProvider(),
    "zhipu": ZhipuProvider(),
}


# ── Models ───────────────────────────────────────────────────────


class RegisterProviderRequest(BaseModel):
    name: str
    provider_type: str = "openai"
    api_base: str = ""
    api_key_env: str = ""
    config: dict[str, Any] = {}


class RegisterModelRequest(BaseModel):
    model_alias: str
    provider: str
    model_id: str
    max_tokens: int = 4096
    cost_per_1k_input: float = 0.0
    cost_per_1k_output: float = 0.0
    enabled: bool = True


# ── Health ───────────────────────────────────────────────────────


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


# ── Provider CRUD ────────────────────────────────────────────────


@app.post("/providers")
async def register_provider(req: RegisterProviderRequest) -> dict:
    provider_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    provider = {
        "id": provider_id,
        "name": req.name,
        "provider_type": req.provider_type,
        "api_base": req.api_base,
        "api_key_env": req.api_key_env,
        "config": req.config,
        "status": "available",
        "created_at": now,
    }
    _providers[provider_id] = provider
    return provider


@app.get("/providers")
async def list_providers() -> list[dict]:
    return list(_providers.values())


@app.get("/providers/{provider_id}")
async def get_provider(provider_id: str) -> dict:
    p = _providers.get(provider_id)
    if not p:
        return {"error": "Provider not found"}
    return p


@app.delete("/providers/{provider_id}")
async def delete_provider(provider_id: str) -> dict:
    if provider_id in _providers:
        del _providers[provider_id]
        return {"deleted": True}
    return {"error": "Provider not found"}


# ── Model management ────────────────────────────────────────────


@app.post("/models")
async def register_model(req: RegisterModelRequest) -> dict:
    now = datetime.now(timezone.utc).isoformat()

    provider_exists = (
        req.provider in _providers
        or req.provider in _builtin_providers
        or any(p["name"] == req.provider for p in _providers.values())
    )
    if not provider_exists:
        return {"error": f"Provider '{req.provider}' not registered"}

    model = {
        "id": str(uuid.uuid4()),
        "model_alias": req.model_alias,
        "provider": req.provider,
        "model_id": req.model_id,
        "max_tokens": req.max_tokens,
        "cost_per_1k_input": req.cost_per_1k_input,
        "cost_per_1k_output": req.cost_per_1k_output,
        "enabled": req.enabled,
        "created_at": now,
    }
    _models[req.model_alias] = model
    _router.add_route(req.model_alias, req.provider, req.model_id)
    return model


@app.get("/models")
async def list_models(provider: str = "") -> list[dict]:
    models = list(_models.values())
    if provider:
        models = [m for m in models if m["provider"] == provider]
    return models


@app.get("/models/{model_alias}")
async def get_model(model_alias: str) -> dict:
    m = _models.get(model_alias)
    if not m:
        return {"error": "Model not found"}
    return m


@app.delete("/models/{model_alias}")
async def delete_model(model_alias: str) -> dict:
    if model_alias in _models:
        del _models[model_alias]
        return {"deleted": True}
    return {"error": "Model not found"}


# ── Routing ──────────────────────────────────────────────────────


@app.post("/route/resolve")
async def resolve_model(body: dict[str, Any]) -> dict:
    """Resolve a model alias to its provider and model ID."""
    alias = body.get("model_alias", "")
    route = _router.resolve(alias)
    if not route:
        return {"error": f"No route for model alias '{alias}'"}

    return {
        "model_alias": alias,
        "provider": route["provider"],
        "model_id": route["model_id"],
        "resolved_at": datetime.now(timezone.utc).isoformat(),
    }


@app.post("/route/cheapest")
async def cheapest_model(body: dict[str, Any]) -> dict:
    """Find the cheapest available model, optionally filtered by provider."""
    provider_filter = body.get("provider", "")
    candidates = list(_models.values())
    if provider_filter:
        candidates = [m for m in candidates if m["provider"] == provider_filter]
    candidates = [m for m in candidates if m.get("enabled", True)]
    if not candidates:
        return {"error": "No available models"}

    cheapest = min(candidates, key=lambda m: m.get("cost_per_1k_input", float("inf")))
    return cheapest
