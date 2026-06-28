"""Gateway configuration."""

import os

import httpx

ORCHESTRATOR_URL = os.getenv("ORCHESTRATOR_URL", "http://localhost:8001")
# gateway→orchestrator 业务路由均挂 /v1 前缀(orchestrator engine.py:367-370)。
# 集中常量便于将来 /v2 切换;两辅助后端(pm/rm)为裸路径,不走此常量。
# 见 docs/mvp-iteration-roadmap.md Phase 0。
ORCHESTRATOR_API = f"{ORCHESTRATOR_URL}/v1"
PROMPT_MANAGER_URL = os.getenv("PROMPT_MANAGER_URL", "http://localhost:8002")
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
