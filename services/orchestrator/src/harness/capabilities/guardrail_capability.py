"""P4 GuardrailCapability — Guardrail.check/check_output → before/after_tool_execute hooks.

ADR: docs/adr/pydantic-ai-v2-adoption.md。把 v2 Guardrail(危险操作 + 敏感信息两阶段检测)
接到 2.0 的 tool 执行 hooks:before_tool_execute 调 check 不通过则 raise ModelRetry(弹回模型
让其改),after_tool_execute 调 check_output 同理。安全常驻(defer_loading=False)。

补安全缺口:v2 Guardrail 在 harness 路径零引用(finding);接 capability 后 native Agent
的 tool call 受护。局限:外部 harness(claw/claude-code)的内部 tool call 仍空转(黑盒,
ADR 接受)。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic_ai import ModelRetry
from pydantic_ai.capabilities import AbstractCapability

from src.tools.guardrail import Guardrail


@dataclass
class GuardrailCapability(AbstractCapability[Any]):
    """常驻安全 capability:tool 调用前/后两阶段校验。"""

    id: str = "guardrail"
    description: str = (
        "Pre/post tool safety — blocks dangerous operations & sensitive-data leakage"
    )
    defer_loading: bool = False
    guardrail: Guardrail | None = None

    async def before_tool_execute(self, ctx, *, call, tool_def, args):
        """调 Guardrail.check;危险参数 → raise ModelRetry 弹回模型。"""
        if self.guardrail is not None:
            ok, reason = await self.guardrail.check(tool_def.name, args)
            if not ok:
                raise ModelRetry(f"Blocked by guardrail: {reason}")
        return args

    async def after_tool_execute(self, ctx, *, call, tool_def, args, result):
        """调 Guardrail.check_output;敏感输出 → raise ModelRetry。"""
        if self.guardrail is not None:
            ok, reason = await self.guardrail.check_output(result)
            if not ok:
                raise ModelRetry(f"Blocked by guardrail (output): {reason}")
        return result
