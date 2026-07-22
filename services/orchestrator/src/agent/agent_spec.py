"""Pydantic schema for agents.yaml (AO2 agent 持久化配置).

P0 配置地基:纯 schema,无运行时行为。对齐设计文档 §4.1/§4.2 的 YAML key
与 OpenClaw VALID_ID_RE 语义。load/resolve/fallback 在 agent_registry.py(P0 后续)。
"""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, model_validator

# OpenClaw VALID_ID_RE 同义:/^[a-z0-9][a-z0-9_-]{0,63}$/。pattern 双重保证 schema 层
# 已拒绝非法 id;normalize 仅用于把外部原始输入(claw name 风格)收敛进此集合。
_ID_PATTERN = r"^[a-z0-9][a-z0-9_-]{0,63}$"
_INVALID_CHARS_RE = re.compile(r"[^a-z0-9_-]+")
_LEADING_DASH_RE = re.compile(r"^-+")
_TRAILING_DASH_RE = re.compile(r"-+$")


def normalize_agent_id(raw: str) -> str:
    """Normalize an external id (claw name 风格) into the VALID_ID_RE set.

    lowercase + 非法 [^a-z0-9_-] 连续段→``-`` + 去首尾 ``-`` + 截 64;
    首字符必须 [a-z0-9](空串/全非法 fallback ``agent``)。
    对齐 OpenClaw ``canonicalizeAccountId``。
    """
    s = (raw or "").strip().lower()
    if not s:
        return "agent"
    s = _INVALID_CHARS_RE.sub("-", s)
    s = _LEADING_DASH_RE.sub("", s)
    s = _TRAILING_DASH_RE.sub("", s)
    s = s[:64]
    s = _TRAILING_DASH_RE.sub("", s)  # 截断后可能再造尾 -
    if not s or not s[0].isalnum():
        return "agent"
    return s


class ToolPolicy(BaseModel):
    """工具白/黑名单(P2 接 ToolBridge,P0 仅声明)。"""

    allow: list[str] = []
    deny: list[str] = []


class CwdEntry(BaseModel):
    """单 cwd scope 项:相对 repo root(path)/切换句柄(label)/初始激活位(default)。"""

    path: str  # 相对 repo root
    label: str | None = None  # None → Path(path).name(registry resolve 时填)
    default: bool = False


class AgentSpec(BaseModel):
    """单 agent 声明(agents.yaml ``agents[]``)。"""

    id: str = Field(..., pattern=_ID_PATTERN)
    default: bool = False
    name: str | None = None
    model: str | None = None
    workspace: str | None = None  # 身份目录(单);None→stateDir 派生
    cwds: list[CwdEntry] = []
    skills: list[str] = []
    instructions: str | None = None
    tools: ToolPolicy = Field(default_factory=ToolPolicy)
    effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    output_type: str | None = None


class Defaults(BaseModel):
    """agents.yaml ``defaults`` 块。"""

    model: str | None = None
    workspace_base: str | None = None
    skills: list[str] = []


class AgentsConfig(BaseModel):
    """agents.yaml 顶层:defaults + agents。"""

    defaults: Defaults
    agents: list[AgentSpec] = Field(..., min_length=1)

    @model_validator(mode="after")
    def _check_uniqueness_and_defaults(self) -> "AgentsConfig":
        ids = [a.id for a in self.agents]
        if len(ids) != len(set(ids)):
            dup = next(i for i in ids if ids.count(i) > 1)
            raise ValueError(f"duplicate agent id: {dup!r}")
        defaults = [a for a in self.agents if a.default]
        if len(defaults) > 1:
            raise ValueError("at most one agent may set default=true")
        for a in self.agents:
            cwd_defaults = [c for c in a.cwds if c.default]
            if len(cwd_defaults) > 1:
                raise ValueError(
                    f"agent {a.id!r}: at most one cwd may set default=true"
                )
        return self
