"""pydantic-ai 2.0 Capabilities — agent-os-v2 自研能力的 Capability 化打包。

ADR: docs/adr/pydantic-ai-v2-adoption.md(P2-P6)。每个能力捆成可组合的
AbstractCapability(tools + instructions + hooks + settings),挂 native Agent。
只在 in-process Agent.run 路径生效(外部 harness claw/claude-code 不经此,ADR 红线)。

P2 ProfileCapability / P3 MemoryCapability / P4 GuardrailCapability /
P5 ObserveCapability / P6 SkillCapabilityFactory。
"""

from .guardrail_capability import GuardrailCapability
from .memory_capability import MemoryCapability
from .memory_writer_capability import MemoryWriterCapability
from .observe_capability import ObserveCapability
from .profile_capability import ProfileCapability
from .skill_capability import SkillCapability, make_skill_capabilities

__all__ = [
    "ProfileCapability",
    "MemoryCapability",
    "MemoryWriterCapability",
    "GuardrailCapability",
    "ObserveCapability",
    "SkillCapability",
    "make_skill_capabilities",
]
