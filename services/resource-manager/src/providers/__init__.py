"""Provider adapters — unified interface to LLM providers."""

import os
from typing import Any, AsyncGenerator

import httpx


class BaseProvider:
    """Base class for LLM provider adapters."""

    name: str = "base"
    base_url: str = ""
    api_key_env: str = ""

    def _get_api_key(self) -> str:
        key = os.environ.get(self.api_key_env, "")
        if not key:
            raise ValueError(f"Missing API key: set {self.api_key_env}")
        return key

    async def complete(self, messages: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
        """Send a completion request."""
        raise NotImplementedError

    async def stream(self, messages: list[dict[str, Any]], **kwargs: Any) -> AsyncGenerator[str, None]:
        """Stream a completion request."""
        raise NotImplementedError
        yield  # make it an async generator  # noqa: unreachable


class OpenAIProvider(BaseProvider):
    """OpenAI API adapter."""

    name = "openai"
    base_url = "https://api.openai.com/v1"
    api_key_env = "OPENAI_API_KEY"

    async def complete(self, messages: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
        model = kwargs.get("model", "gpt-4o-mini")
        temperature = kwargs.get("temperature", 0.7)
        max_tokens = kwargs.get("max_tokens", 1024)

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self._get_api_key()}"},
                json={
                    "model": model,
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                },
            )
            resp.raise_for_status()
            return resp.json()

    async def stream(self, messages: list[dict[str, Any]], **kwargs: Any) -> AsyncGenerator[str, None]:
        model = kwargs.get("model", "gpt-4o-mini")
        temperature = kwargs.get("temperature", 0.7)
        max_tokens = kwargs.get("max_tokens", 1024)

        async with httpx.AsyncClient(timeout=60.0) as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self._get_api_key()}"},
                json={
                    "model": model,
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                    "stream": True,
                },
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    payload = line[6:]
                    if payload.strip() == "[DONE]":
                        break
                    import json
                    chunk = json.loads(payload)
                    delta = chunk.get("choices", [{}])[0].get("delta", {})
                    content = delta.get("content", "")
                    if content:
                        yield content


class AnthropicProvider(BaseProvider):
    """Anthropic API adapter."""

    name = "anthropic"
    base_url = "https://api.anthropic.com/v1"
    api_key_env = "ANTHROPIC_API_KEY"

    async def complete(self, messages: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
        model = kwargs.get("model", "claude-sonnet-4-20250514")
        max_tokens = kwargs.get("max_tokens", 1024)
        temperature = kwargs.get("temperature", 0.7)

        # Anthropic expects system as a top-level param, not in messages
        system_msg = ""
        chat_messages = []
        for m in messages:
            if m["role"] == "system":
                system_msg += m["content"] + "\n"
            else:
                chat_messages.append(m)

        body: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": chat_messages,
        }
        if system_msg.strip():
            body["system"] = system_msg.strip()
        if temperature is not None:
            body["temperature"] = temperature

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{self.base_url}/messages",
                headers={
                    "x-api-key": self._get_api_key(),
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json=body,
            )
            resp.raise_for_status()
            return resp.json()

    async def stream(self, messages: list[dict[str, Any]], **kwargs: Any) -> AsyncGenerator[str, None]:
        model = kwargs.get("model", "claude-sonnet-4-20250514")
        max_tokens = kwargs.get("max_tokens", 1024)
        temperature = kwargs.get("temperature", 0.7)

        system_msg = ""
        chat_messages = []
        for m in messages:
            if m["role"] == "system":
                system_msg += m["content"] + "\n"
            else:
                chat_messages.append(m)

        body: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": chat_messages,
            "stream": True,
        }
        if system_msg.strip():
            body["system"] = system_msg.strip()
        if temperature is not None:
            body["temperature"] = temperature

        async with httpx.AsyncClient(timeout=60.0) as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/messages",
                headers={
                    "x-api-key": self._get_api_key(),
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json=body,
            ) as resp:
                resp.raise_for_status()
                import json
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    payload = line[6:]
                    try:
                        chunk = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    if chunk.get("type") == "content_block_delta":
                        text = chunk.get("delta", {}).get("text", "")
                        if text:
                            yield text


class ZhipuProvider(BaseProvider):
    """Zhipu (GLM) API adapter — OpenAI-compatible endpoint."""

    name = "zhipu"
    base_url = "https://open.bigmodel.cn/api/paas/v4"
    api_key_env = "ZHIPU_API_KEY"

    async def complete(self, messages: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
        model = kwargs.get("model", "glm-4-flash")
        temperature = kwargs.get("temperature", 0.7)
        max_tokens = kwargs.get("max_tokens", 1024)

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self._get_api_key()}"},
                json={
                    "model": model,
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                },
            )
            resp.raise_for_status()
            return resp.json()

    async def stream(self, messages: list[dict[str, Any]], **kwargs: Any) -> AsyncGenerator[str, None]:
        model = kwargs.get("model", "glm-4-flash")
        temperature = kwargs.get("temperature", 0.7)
        max_tokens = kwargs.get("max_tokens", 1024)

        async with httpx.AsyncClient(timeout=60.0) as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self._get_api_key()}"},
                json={
                    "model": model,
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                    "stream": True,
                },
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    payload = line[6:]
                    if payload.strip() == "[DONE]":
                        break
                    import json
                    chunk = json.loads(payload)
                    delta = chunk.get("choices", [{}])[0].get("delta", {})
                    content = delta.get("content", "")
                    if content:
                        yield content
