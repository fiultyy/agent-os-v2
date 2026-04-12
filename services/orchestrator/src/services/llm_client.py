"""Lightweight LLM client using OpenAI-compatible chat completions API."""

from __future__ import annotations

import os
from typing import Any

import httpx


class LLMError(Exception):
    """Raised when the LLM call fails."""


class LLMClient:
    """Lightweight LLM client using OpenAI-compatible chat completions API."""

    def __init__(self) -> None:
        self.base_url = os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1")
        self.api_key = os.environ.get("LLM_API_KEY", os.environ.get("OPENAI_API_KEY", ""))
        self.default_model = os.environ.get("LLM_MODEL", "gpt-4o-mini")

    async def chat(self, messages: list[dict[str, Any]], model: str | None = None, **kwargs: Any) -> str:
        """Send messages and return the assistant content string.

        Raises:
            LLMError: If the API key is missing or the API call fails.
        """
        if not self.api_key:
            raise LLMError("No API key configured — set LLM_API_KEY or OPENAI_API_KEY")

        url = f"{self.base_url}/chat/completions"
        payload: dict[str, Any] = {
            "model": model or self.default_model,
            "messages": messages,
            "temperature": kwargs.get("temperature", 0.7),
            "max_tokens": kwargs.get("max_tokens", 1024),
        }

        async with httpx.AsyncClient(timeout=60.0) as client:
            try:
                resp = await client.post(
                    url,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()
                return data["choices"][0]["message"]["content"]
            except httpx.HTTPStatusError as exc:
                raise LLMError(f"HTTP {exc.response.status_code}: {exc.response.text[:200]}") from exc
            except httpx.RequestError as exc:
                raise LLMError(f"Request failed: {exc}") from exc


# Module-level singleton
llm_client = LLMClient()
