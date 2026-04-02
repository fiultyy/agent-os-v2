"""Provider adapters — unified interface to LLM providers."""

from typing import Any


class BaseProvider:
    """Base class for LLM provider adapters."""

    name: str = "base"

    async def complete(self, messages: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
        """Send a completion request."""
        raise NotImplementedError

    async def stream(self, messages: list[dict[str, Any]], **kwargs: Any):
        """Stream a completion request."""
        raise NotImplementedError
        yield  # make it an async generator


class OpenAIProvider(BaseProvider):
    """OpenAI API adapter."""
    name = "openai"


class AnthropicProvider(BaseProvider):
    """Anthropic API adapter."""
    name = "anthropic"


class ZhipuProvider(BaseProvider):
    """Zhipu (GLM) API adapter."""
    name = "zhipu"
