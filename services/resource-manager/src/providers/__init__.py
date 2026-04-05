"""Provider adapters — unified interface to LLM providers."""

import asyncio
import json
import logging
import os
from abc import ABC, abstractmethod
from typing import Any, AsyncGenerator

import httpx

logger = logging.getLogger(__name__)

RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
MAX_RETRIES = 3
BACKOFF_BASE = 1.0  # seconds


async def _request_with_retry(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    **kwargs: Any,
) -> httpx.Response:
    """Execute an HTTP request with exponential-backoff retry on transient errors."""
    last_exc: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            resp = await client.request(method, url, **kwargs)
            if resp.status_code not in RETRYABLE_STATUS_CODES:
                return resp
            last_exc = httpx.HTTPStatusError(
                f"Retryable HTTP {resp.status_code}",
                request=resp.request,
                response=resp,
            )
            logger.warning("HTTP %d on attempt %d/%d for %s", resp.status_code, attempt + 1, MAX_RETRIES, url)
        except httpx.TransportError as exc:
            last_exc = exc
            logger.warning("Transport error on attempt %d/%d for %s: %s", attempt + 1, MAX_RETRIES, url, exc)

        if attempt < MAX_RETRIES - 1:
            delay = BACKOFF_BASE * (2 ** attempt)
            await asyncio.sleep(delay)

    raise last_exc or httpx.TransportError("All retries exhausted")


class BaseProvider(ABC):
    """Base class for LLM provider adapters."""

    name: str = "base"
    base_url: str = ""
    api_key_env: str = ""

    def _get_api_key(self) -> str:
        key = os.environ.get(self.api_key_env, "")
        if not key:
            raise ValueError(f"Missing API key: set {self.api_key_env}")
        return key

    @abstractmethod
    async def complete(self, messages: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
        """Send a completion request."""

    @abstractmethod
    async def stream(self, messages: list[dict[str, Any]], **kwargs: Any) -> AsyncGenerator[str, None]:
        """Stream a completion request."""


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
            resp = await _request_with_retry(
                client,
                "POST",
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

        # Retry connection establishment for stream (not individual chunks)
        last_exc: Exception | None = None
        for attempt in range(MAX_RETRIES):
            try:
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
                            chunk = json.loads(payload)
                            delta = chunk.get("choices", [{}])[0].get("delta", {})
                            content = delta.get("content", "")
                            if content:
                                yield content
                    return  # success, exit retry loop
            except (httpx.HTTPStatusError, httpx.TransportError) as exc:
                last_exc = exc
                if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code not in RETRYABLE_STATUS_CODES:
                    raise
                logger.warning("Stream attempt %d/%d failed: %s", attempt + 1, MAX_RETRIES, exc)
                if attempt < MAX_RETRIES - 1:
                    delay = BACKOFF_BASE * (2 ** attempt)
                    await asyncio.sleep(delay)

        raise last_exc or httpx.TransportError("Stream retries exhausted")


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
            resp = await _request_with_retry(
                client,
                "POST",
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

        last_exc: Exception | None = None
        for attempt in range(MAX_RETRIES):
            try:
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
                return  # success, exit retry loop
            except (httpx.HTTPStatusError, httpx.TransportError) as exc:
                last_exc = exc
                if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code not in RETRYABLE_STATUS_CODES:
                    raise
                logger.warning("Stream attempt %d/%d failed: %s", attempt + 1, MAX_RETRIES, exc)
                if attempt < MAX_RETRIES - 1:
                    delay = BACKOFF_BASE * (2 ** attempt)
                    await asyncio.sleep(delay)

        raise last_exc or httpx.TransportError("Stream retries exhausted")


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
            resp = await _request_with_retry(
                client,
                "POST",
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

        last_exc: Exception | None = None
        for attempt in range(MAX_RETRIES):
            try:
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
                            chunk = json.loads(payload)
                            delta = chunk.get("choices", [{}])[0].get("delta", {})
                            content = delta.get("content", "")
                            if content:
                                yield content
                return  # success, exit retry loop
            except (httpx.HTTPStatusError, httpx.TransportError) as exc:
                last_exc = exc
                if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code not in RETRYABLE_STATUS_CODES:
                    raise
                logger.warning("Stream attempt %d/%d failed: %s", attempt + 1, MAX_RETRIES, exc)
                if attempt < MAX_RETRIES - 1:
                    delay = BACKOFF_BASE * (2 ** attempt)
                    await asyncio.sleep(delay)

        raise last_exc or httpx.TransportError("Stream retries exhausted")
