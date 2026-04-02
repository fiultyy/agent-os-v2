"""Gateway configuration."""

import os

ORCHESTRATOR_URL = os.getenv("ORCHESTRATOR_URL", "http://localhost:8001")
PROMPT_MANAGER_URL = os.getenv("PROMPT_MANAGER_URL", "http://localhost:8002")
CONVERSATION_OBSERVER_URL = os.getenv(
    "CONVERSATION_OBSERVER_URL", "http://localhost:8003"
)
RESOURCE_MANAGER_URL = os.getenv("RESOURCE_MANAGER_URL", "http://localhost:8004")
