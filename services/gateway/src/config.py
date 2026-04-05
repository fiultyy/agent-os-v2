"""Gateway configuration."""

import os

import httpx

ORCHESTRATOR_URL = os.getenv("ORCHESTRATOR_URL", "http://localhost:8001")
PROMPT_MANAGER_URL = os.getenv("PROMPT_MANAGER_URL", "http://localhost:8002")
CONVERSATION_OBSERVER_URL = os.getenv(
    "CONVERSATION_OBSERVER_URL", "http://localhost:8003"
)
RESOURCE_MANAGER_URL = os.getenv("RESOURCE_MANAGER_URL", "http://localhost:8004")

# ── Auth configuration ────────────────────────────────────────────────────────

AUTH_ENABLED: bool = os.getenv("AUTH_ENABLED", "false").lower() in ("true", "1", "yes")

AUTH_API_KEYS: list[str] = [
    k.strip() for k in os.getenv("AUTH_API_KEYS", "").split(",") if k.strip()
]

JWT_PRIVATE_KEY_PATH: str | None = os.getenv("JWT_PRIVATE_KEY_PATH")
JWT_PUBLIC_KEY_PATH: str | None = os.getenv("JWT_PUBLIC_KEY_PATH")

SERVICE_AUTH_KEY: str | None = os.getenv("SERVICE_AUTH_KEY")

# ── Shared HTTP client ───────────────────────────────────────────────────────

http_client = httpx.AsyncClient(timeout=60.0)
