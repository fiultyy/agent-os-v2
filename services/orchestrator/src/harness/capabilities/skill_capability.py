"""P6 SkillCapability / make_skill_capabilities — SKILL.md → defer-able Capability.

ADR: docs/adr/pydantic-ai-v2-adoption.md。把 v2 SkillLoader 扫出的 SkillEntry 包成
2.0 Capability(**defer_loading=True**,渐进按需加载)。

**glm defer 实测通过(2026-07-22 e2e 3 场景验证)**:旧 docstring 称"glm-5.2 不调 load
meta-tool → eager 降级"是误判 —— pydantic-ai 官方 defer(`defer_loading=True` + `id` →
框架自动注入 `DeferredCapabilityLoader`:catalog 索引进 dynamic prefix + 显式
`load_capability` tool)在 glm 下**强/弱 prompt 都调 load_capability,正文按需载入**。
故改回 defer(渐进按需),正文不常驻 system,省 token + 保 cache prefix 稳定(catalog
是 stable dynamic instruction,跨轮 byte-identical)。

机制:catalog(Stage1 = 索引 prefix,模型可见 skill 名+描述)→ 模型调
`load_capability(id)`(Stage2 = 载入 SKILL.md 正文 instructions + 激活)。替代
skill_executor 的自管 Stage1/Stage2 逻辑。

requires.env 校验接 before_tool_execute(缺 env → ModelRetry 弹回模型);requires.tools
校验需知当前 toolset,P6 暂只校验 env。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic_ai import ModelRetry
from pydantic_ai.capabilities import AbstractCapability

from src.skills.skill_loader import SkillEntry, SkillLoader

# 去 frontmatter(--- ... ---)取正文
_FRONTMATTER_BODY_RE = re.compile(r"\A---\s*\n.*?\n---\s*\n", re.DOTALL)


def _read_skill_body(path: Path) -> str:
    """读 SKILL.md 正文(去 YAML frontmatter)。"""
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
    return _FRONTMATTER_BODY_RE.sub("", content, count=1).strip()


@dataclass
class SkillCapability(AbstractCapability[None]):
    """单个 SKILL.md → defer-able capability(模型按需 load 注入正文指令)。"""

    id: str = "skill"
    description: str = ""
    defer_loading: bool = True  # 渐进按需加载(glm defer e2e 验证通过,见模块 docstring)
    skill: Any = None  # SkillEntry

    def get_instructions(self) -> str:
        return _read_skill_body(self.skill.location) if self.skill is not None else ""

    async def before_tool_execute(self, ctx, *, call, tool_def, args):
        # requires.env 缺失 → 弹回模型(提示补 env)
        if self.skill is not None:
            for env_var in getattr(self.skill.requires, "env", []) or []:
                if not os.getenv(env_var):
                    raise ModelRetry(
                        f"Skill {self.id} requires env var {env_var!r} (not set)"
                    )
        return args


def make_skill_capabilities(loader: SkillLoader) -> list[SkillCapability]:
    """SkillLoader.scan() → list[SkillCapability](每个 enabled skill 一个)。"""
    caps: list[SkillCapability] = []
    for entry in loader.scan():
        if not entry.is_enabled():
            continue
        caps.append(SkillCapability(
            id=entry.name,
            description=entry.description or f"skill {entry.name}",
            defer_loading=True,
            skill=entry,
        ))
    return caps
