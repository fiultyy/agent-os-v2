"""P2 ProfileCapability — AgentBaseProfile → pydantic-ai 2.0 AbstractCapability.

ADR: docs/adr/pydantic-ai-v2-adoption.md。把 v2 的分层 profile(L0-L4 → system prompt)
包成 2.0 的 get_instructions,挂 native Agent 自动注入 system prompt。

profile 是常驻指令(defer_loading=False):每次 run 都生效,不靠模型 load_capability。
(按层 defer 会破坏 L0-L5 确定性叠加,ADR 不推荐。)
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic_ai.capabilities import AbstractCapability

from src.agent.profile import AgentBaseProfile


@dataclass
class ProfileCapability(AbstractCapability[None]):
    """常驻 system prompt capability:返 AgentBaseProfile.compile()。"""

    id: str = "profile"
    description: str = "Agent base profile — L0-L4 layered system prompt (SOUL/AGENTS.md)"
    defer_loading: bool = False
    profile: AgentBaseProfile | None = None

    def get_instructions(self) -> str:
        """L0-L4 → system prompt;profile 未注入返空串(no-op capability)。"""
        return self.profile.compile() if self.profile is not None else ""
