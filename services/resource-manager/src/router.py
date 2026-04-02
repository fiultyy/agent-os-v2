"""Model router — routes requests to the appropriate provider/model."""

from typing import Any


class ModelRouter:
    """Routes LLM requests based on model name, cost, latency, and availability."""

    def __init__(self) -> None:
        self._routes: dict[str, dict[str, Any]] = {}

    def add_route(self, model_alias: str, provider: str, model_id: str) -> None:
        """Register a model route."""
        self._routes[model_alias] = {"provider": provider, "model_id": model_id}

    def resolve(self, model_alias: str) -> dict[str, Any] | None:
        """Resolve a model alias to provider + model_id."""
        return self._routes.get(model_alias)
