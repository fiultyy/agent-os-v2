"""LLM client — dual-channel (OpenAI-compatible + Anthropic-compatible).

P2 adds an Anthropic-compatible channel alongside the existing
OpenAI-compatible one:

- ``format=openai`` (default, backward compatible): ``/chat/completions``.
  ``cache_control`` is NOT sent — Zhipu's OpenAI endpoint ignores it and
  relies on a stable prefix for server-side auto-caching.
- ``format=anthropic``: ``/v1/messages`` reusing the claude code config
  (``ANTHROPIC_BASE_URL`` / ``ANTHROPIC_AUTH_TOKEN`` = Zhipu anthropic
  endpoint, which honours ``cache_control``). Injects a ``cache_control``
  breakpoint at the static-prefix boundary when ``static_count`` is given.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from src.services.prompt_cache import apply_cache_control


class LLMError(Exception):
    """Raised when the LLM call fails."""


class LLMClient:
    """Dual-channel LLM client (OpenAI-compatible + Anthropic-compatible)."""

    def __init__(
        self,
        *,
        format: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        default_model: str | None = None,
        anthropic_base_url: str | None = None,
        anthropic_api_key: str | None = None,
        anthropic_model: str | None = None,
    ) -> None:
        # #4: 全部参数默认 None → 走原 os.environ 分支(字节级向后兼容,
        # ``LLMClient()`` 无参调用零破坏)。显式传参 → 覆盖 env(供 side agent
        # 走独立 OpenAI / glm-4-flash 通道)。
        # ── OpenAI-compatible channel ────────────────────────────────
        self.base_url = base_url or os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1")
        self.api_key = api_key or os.environ.get("LLM_API_KEY", os.environ.get("OPENAI_API_KEY", ""))
        self.default_model = default_model or os.environ.get("LLM_MODEL", "gpt-4o-mini")

        # ── Channel selection + cache config ─────────────────────────
        self.format = (format or os.environ.get("LLM_API_FORMAT", "openai")).lower()
        self.cache_enabled = os.environ.get("LLM_CACHE_ENABLED", "1") == "1"
        self.cache_ttl = os.environ.get("LLM_CACHE_TTL", "5m")

        # ── Anthropic-compatible channel (reuses claude code config) ─
        self.anthropic_base_url = anthropic_base_url or os.environ.get(
            "ANTHROPIC_BASE_URL", "https://open.bigmodel.cn/api/anthropic"
        )
        self.anthropic_api_key = anthropic_api_key or os.environ.get("ANTHROPIC_AUTH_TOKEN", "")
        self.anthropic_model = anthropic_model or os.environ.get(
            "LLM_ANTHROPIC_MODEL",
            os.environ.get("ANTHROPIC_DEFAULT_SONNET_MODEL", "glm-5-turbo"),
        )

        # Last response usage (Anthropic channel exposes cache fields).
        self.last_usage: dict[str, Any] | None = None

    async def chat(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        static_count: int | None = None,
        **kwargs: Any,
    ) -> str:
        """Send messages and return the assistant content string.

        Args:
            messages: Message list (OpenAI-style roles).
            model: Model name (OpenAI channel only; Anthropic channel uses
                ``LLM_ANTHROPIC_MODEL`` and ignores this).
            static_count: Number of leading static messages. Anthropic
                channel uses it to place the cache_control breakpoint;
                OpenAI channel ignores it.

        Raises:
            LLMError: If the API key is missing or the API call fails.
        """
        if self.format == "anthropic":
            return await self._chat_anthropic(messages, static_count, **kwargs)
        return await self._chat_openai(messages, model, **kwargs)

    async def _chat_openai(
        self,
        messages: list[dict[str, Any]],
        model: str | None,
        **kwargs: Any,
    ) -> str:
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
                return resp.json()["choices"][0]["message"]["content"]
            except httpx.HTTPStatusError as exc:
                raise LLMError(f"HTTP {exc.response.status_code}: {exc.response.text[:200]}") from exc
            except httpx.RequestError as exc:
                raise LLMError(f"Request failed: {exc}") from exc

    async def _chat_anthropic(
        self,
        messages: list[dict[str, Any]],
        static_count: int | None,
        **kwargs: Any,
    ) -> str:
        if not self.anthropic_api_key:
            raise LLMError("No Anthropic key configured — set ANTHROPIC_AUTH_TOKEN")

        # Inject cache_control at the static-prefix boundary (Zhipu anthropic
        # endpoint honours it).
        sc = static_count or 0
        if self.cache_enabled and sc > 0:
            messages = apply_cache_control(messages, sc, self.cache_ttl)

        system, anthropic_msgs = self._to_anthropic(messages, sc)

        url = f"{self.anthropic_base_url}/v1/messages"
        payload: dict[str, Any] = {
            "model": self.anthropic_model,  # ignore OpenAI agent model
            "messages": anthropic_msgs,
            "max_tokens": kwargs.get("max_tokens", 1024),
        }
        if system is not None:
            payload["system"] = system
        if "temperature" in kwargs:
            payload["temperature"] = kwargs["temperature"]

        headers = {
            "x-api-key": self.anthropic_api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

        async with httpx.AsyncClient(timeout=60.0) as client:
            try:
                resp = await client.post(url, headers=headers, json=payload)
                resp.raise_for_status()
                data = resp.json()
                self.last_usage = data.get("usage")
                # content is a list of blocks; return first text block
                content = data.get("content", [])
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        return block.get("text", "")
                return ""
            except httpx.HTTPStatusError as exc:
                raise LLMError(f"HTTP {exc.response.status_code}: {exc.response.text[:200]}") from exc
            except httpx.RequestError as exc:
                raise LLMError(f"Request failed: {exc}") from exc

    @staticmethod
    def _to_anthropic(
        messages: list[dict[str, Any]],
        static_count: int,
    ) -> tuple[Any, list[dict[str, Any]]]:
        """Convert OpenAI-style messages to Anthropic (system, messages).

        - System messages within the static prefix
          (``messages[:static_count]``) → Anthropic top-level ``system``.
          cache_control markers preserved (emitted as a content-block list
          when any block carries one).
        - R2: the compiler injects recalled memory into the *user message
          tail* (fenced), so there are no dynamic system messages anymore.
          A system message beyond the static boundary now raises — it means
          the compiler contract was broken (pre-R2 this was silently
          demoted to a ``[Memory context]`` user message, which masked the
          bug and broke the OpenAI channel's prefix). When
          ``static_count == 0`` (no cache hint) every system message lifts
          to top-level system.
        - user/assistant messages → kept as-is (content str or list).
        """
        system_blocks: list[dict[str, Any]] = []
        convo: list[dict[str, Any]] = []

        for i, msg in enumerate(messages):
            role = msg.get("role")
            content = msg.get("content")

            if role == "system":
                # R2: compiler injects recalled memory into the user message
                # tail (fenced), so every system message belongs to the static
                # prefix (base + tools). A system message beyond the boundary
                # means the compiler contract was broken — raise rather than
                # silently demote (the pre-R2 ``[Memory context]`` fallback
                # masked the bug and broke the OpenAI channel prefix). When
                # static_count == 0 (no cache hint) every system message lifts.
                if static_count > 0 and i >= static_count:
                    raise ValueError(
                        f"system message at index {i} is beyond the static "
                        f"prefix (static_count={static_count}); the compiler "
                        f"should inject dynamic content into the user message "
                        f"tail (R2), not as a system message"
                    )
                if isinstance(content, list):
                    system_blocks.extend(content)
                elif isinstance(content, str):
                    system_blocks.append({"type": "text", "text": content})
            else:
                convo.append({"role": role, "content": content if content is not None else ""})

        has_cache = any(
            isinstance(b, dict) and "cache_control" in b for b in system_blocks
        )
        if has_cache:
            system: Any = system_blocks
        elif system_blocks:
            system = "\n\n".join(
                b.get("text", "") for b in system_blocks if isinstance(b, dict)
            )
        else:
            system = None

        return system, convo
