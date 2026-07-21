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
        # Native tool_use parsed from the last response (function-calling).
        # Anthropic: ``{"name": str, "input": dict}`` (first tool_use block)
        # or ``None`` when no tool was requested. OpenAI channel populates the
        # same shape from ``tool_calls`` when present. Side-agent callers that
        # never pass ``tools`` leave this ``None`` (zero regression).
        self.last_tool_use: dict[str, Any] | None = None

    async def chat(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        static_count: int | None = None,
        tools: list[dict[str, Any]] | None = None,
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
            tools: Optional list of tool schemas for *native* function-calling.
                Anthropic schema: ``{name, description, input_schema}`` — these
                are forwarded verbatim. OpenAI schema: ``{type:"function",
                function:{name, description, parameters}}``. When ``None``
                (default) tool_use is disabled — full backward compatibility.

        Tool-use results:
            When the model emits a native ``tool_use`` block, ``self.last_tool_use``
            is set to ``{"name": <tool>, "input": <args dict>}`` *before* this
            method returns. Callers that care about tool calls read
            ``last_tool_use`` after ``await chat(...)``. Callers that ignore it
            are unaffected (it stays ``None`` when no tools are passed).

        Raises:
            LLMError: If the API key is missing or the API call fails.
        """
        # Reset per-call: a caller reusing the client must not observe a
        # stale tool_use from a previous turn.
        self.last_tool_use = None
        if self.format == "anthropic":
            return await self._chat_anthropic(messages, static_count, tools=tools, **kwargs)
        return await self._chat_openai(messages, model, tools=tools, **kwargs)

    async def _chat_openai(
        self,
        messages: list[dict[str, Any]],
        model: str | None,
        tools: list[dict[str, Any]] | None = None,
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
        if tools:
            # OpenAI tool schema: {"type":"function","function":{name,description,
            # parameters}}. Accept either pre-shaped OpenAI schemas or bare
            # Anthropic-shaped ones ({name,description,input_schema}) and normalize.
            payload["tools"] = [
                t if t.get("type") == "function"
                else {
                    "type": "function",
                    "function": {
                        "name": t["name"],
                        "description": t.get("description", ""),
                        "parameters": t.get("input_schema") or t.get("parameters") or {},
                    },
                }
                for t in tools
            ]
            payload["tool_choice"] = "auto"

        async with httpx.AsyncClient(timeout=60.0) as client:
            try:
                resp = await client.post(
                    url,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        # 明确 client 标识(非匿名),provider 可识别为正规 client
                        # 而非可疑/匿名流量。env 可覆盖。不伪造第三方身份。
                        "user-agent": os.environ.get("LLM_USER_AGENT", "agent-os/0.2.0"),
                    },
                    json=payload,
                )
                resp.raise_for_status()
                msg = resp.json()["choices"][0]["message"]
                # Native tool_calls (OpenAI shape). Parse the first function
                # call into the normalized {name, input} tool_use shape so the
                # orchestrator's _node_tool works uniformly across channels.
                if tools and msg.get("tool_calls"):
                    tc = msg["tool_calls"][0]
                    fn = tc.get("function", {})
                    import json as _json
                    try:
                        args_in = _json.loads(fn.get("arguments") or "{}")
                    except (ValueError, TypeError):
                        args_in = {}
                    self.last_tool_use = {"name": fn.get("name", ""), "input": args_in}
                return msg.get("content") or ""
            except httpx.HTTPStatusError as exc:
                raise LLMError(f"HTTP {exc.response.status_code}: {exc.response.text[:200]}") from exc
            except httpx.RequestError as exc:
                raise LLMError(f"Request failed: {exc}") from exc

    async def _chat_anthropic(
        self,
        messages: list[dict[str, Any]],
        static_count: int | None,
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> str:
        if not self.anthropic_api_key:
            raise LLMError("No Anthropic key configured — set ANTHROPIC_AUTH_TOKEN")

        # Inject cache_control at the static-prefix boundary when the caller
        # supplies ``static_count`` (Zhipu anthropic endpoint honours it).
        # ponytail: budget gating removed — native ModelSettings
        # (routes.py anthropic_cache_instructions/tool_definitions="5m") is the
        # actual cache source on the main path; production callers never pass
        # static_count so the budget branch was dead. apply_cache_control kept
        # as a pure helper for future messages-side breakpoints.
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
        # Native function-calling: forward tool schemas verbatim (Anthropic
        # shape: {name, description, input_schema}). tool_choice="auto" lets
        # the model decide; the stop_reason becomes "tool_use" when chosen.
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = {"type": "auto"}

        headers = {
            "x-api-key": self.anthropic_api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
            # 明确 client 标识(非匿名),provider 可识别为正规 client 而非
            # 可疑/匿名流量。env 可覆盖(LLM_USER_AGENT)。不伪造第三方
            # (Claude Code / 智谱 zcode)身份 —— 诚实标识 agent-os 自己。
            "user-agent": os.environ.get("LLM_USER_AGENT", "agent-os/0.2.0"),
        }

        async with httpx.AsyncClient(timeout=60.0) as client:
            try:
                resp = await client.post(url, headers=headers, json=payload)
                resp.raise_for_status()
                data = resp.json()
                self.last_usage = data.get("usage")
                # content is a list of blocks. Parse native tool_use blocks
                # into the normalized {name, input} shape (first one wins) so
                # the orchestrator's _node_tool executes it. Text blocks remain
                # the primary return value (backward compatible).
                content = data.get("content", [])
                text_out = ""
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    btype = block.get("type")
                    if btype == "text" and not text_out:
                        text_out = block.get("text", "")
                    elif btype == "tool_use" and self.last_tool_use is None:
                        self.last_tool_use = {
                            "name": block.get("name", ""),
                            "input": block.get("input", {}) or {},
                        }
                return text_out
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
