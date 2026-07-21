"""P2 ProfileCapability — AgentBaseProfile → pydantic-ai 2.0 AbstractCapability。

ADR-1(docs/adr/native-v2-wiring.md):分层 Capability 化 — 每 LayerProfile → 独立
LayerCapability(get_instructions 返单层 content 带 `=== source (Lx) ===` 标记)。
make_profile_capabilities 按 L0→L4 序生成,routes.py caps extend 后 pydantic-ai 按 caps
序拼 system prompt → L0→L4 叠加。常驻(defer_loading=False),保 L0-L5 确定性叠加。

老 ProfileCapability(整体 compile)保留作单 capa 兼容;新代码用 LayerCapability。
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic_ai.capabilities import AbstractCapability

from src.agent.profile import AgentBaseProfile, LayerProfile
from src.harness.capabilities._stable_cache import _cached


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


@dataclass
class LayerCapability(AbstractCapability[None]):
    """单层 profile → 独立 capability(ADR-1 分层 capa 化)。

    每层一个实例,pydantic-ai 按 caps 序拼 system prompt → L0→L4 叠加。
    defer_loading=False 常驻(保 L0-L5 确定性叠加;ADR-1 红线)。
    id 须 per-instance 唯一(pydantic-ai 校验),格式 `layer_{L}_{source}`。
    """

    layer: int = 0
    source: str = ""
    content: str = ""
    id: str = "layer"
    defer_loading: bool = False

    def get_instructions(self) -> str:
        # G1: stable prefix hash 缓存 —— (layer, source, content) 决定输出,
        # pydantic-ai 每 .run() 重调,缓存免每轮重拼。content 为空也走同路径。
        return _cached(
            (self.layer, self.source, self.content),
            lambda: f"=== {self.source} (L{self.layer}) ===\n{self.content}",
        )


def make_profile_capabilities(profile: AgentBaseProfile | None) -> list[LayerCapability]:
    """AgentBaseProfile → list[LayerCapability](每 LayerProfile 一条,L0→L4 序)。

    profile None → 返 [](routes None-safe)。L0→L4 序匹配 AgentBaseProfile.compile,
    pydantic-ai 按 caps 序拼 system prompt。id per-instance 唯一(`layer_{L}_{source}`)。
    """
    if profile is None:
        return []
    caps: list[LayerCapability] = []
    for layer in range(5):
        for lp in profile.layers.get(layer, []):
            caps.append(LayerCapability(
                layer=layer, source=lp.source, content=lp.content,
                id=f"layer_{layer}_{lp.source}",
            ))
    return caps
