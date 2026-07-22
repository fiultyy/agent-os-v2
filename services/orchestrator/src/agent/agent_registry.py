"""Process-level AgentRegistry (P0 配置地基).

loads agents.yaml → AgentsConfig, builds id→AgentSpec map, resolves per-agent
workspace(stateDir 派生)与 cwd scope(repo root 相对)。任何加载失败→降级单
native(向后兼容:agents.yaml 缺失时引擎启动不崩)。对齐设计文档 §4.3 / §5 / §6。
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from .agent_spec import (
    AgentSpec,
    AgentsConfig,
    normalize_agent_id,
)

logger = logging.getLogger(__name__)

_DEFAULT_STATE_DIR = "~/.agent-os"


def _state_dir(explicit: str | None = None) -> str:
    return os.path.expanduser(explicit or os.getenv("AO2_STATE_DIR", _DEFAULT_STATE_DIR))


@dataclass
class ResolvedCwd:
    """运行时算出的绝对 cwd 项(resolve_cwd_scope 返回)。

    与 YAML schema 的 CwdEntry 解耦:path_abs 不在配置出现,resolver 填。
    """

    path_abs: str
    label: str
    default: bool = False


class AgentRegistry:
    """进程级单例:id→AgentSpec map + workspace/cwd 解析。"""

    def __init__(self) -> None:
        self._agents: dict[str, AgentSpec] = {}
        self._default_id: str | None = None

    # ------------------------------------------------------------------ load
    @classmethod
    def load(cls, path: str | None = None) -> "AgentRegistry":
        """Load agents.yaml → registry. 任何失败→降级单 native(不 raise)。

        path 优先级 = path 参数 > env AO2_AGENTS_CONFIG > '<repo>/agents.yaml'
        > '<stateDir>/agents.yaml'。
        """
        registry = cls()
        config_path = cls._locate_config(path)
        if config_path is None:
            logger.warning("agents.yaml not found; falling back to single native agent")
            return registry._fallback_native()

        try:
            yaml = __import__("yaml")
            with open(config_path, "r", encoding="utf-8") as f:
                raw = yaml.safe_load(f)
            if not isinstance(raw, dict):
                raise ValueError(f"agents.yaml root must be a mapping, got {type(raw).__name__}")
            # defaults 可缺(YAML 只给 agents)→ 补默认 model=None
            raw.setdefault("defaults", {"model": None})
            cfg = AgentsConfig.model_validate(raw)
        except Exception as exc:  # pyyaml 未装 / 文件坏 / 校验错 / 缺失
            logger.warning("agents.yaml load failed (%s); falling back to single native agent", exc)
            return registry._fallback_native()

        for spec in cfg.agents:
            norm_id = normalize_agent_id(spec.id)  # 幂等(合法 id 原样过)
            spec_id = norm_id
            registry._agents[spec_id] = spec
            if spec.default:
                registry._default_id = spec_id
        # 无显式 default → 取第一个
        if registry._default_id is None and registry._agents:
            registry._default_id = next(iter(registry._agents))
        return registry

    @staticmethod
    def _locate_config(path: str | None) -> Path | None:
        candidates: list[Path] = []
        if path:
            candidates.append(Path(path).expanduser())
        env_cfg = os.getenv("AO2_AGENTS_CONFIG")
        if env_cfg:
            candidates.append(Path(env_cfg).expanduser())
        repo_root = os.getenv("AO2_REPO_ROOT", str(Path.cwd()))
        candidates.append(Path(repo_root) / "agents.yaml")
        candidates.append(Path(_state_dir()) / "agents.yaml")
        seen: set[str] = set()
        for c in candidates:
            key = str(c)
            if key in seen:
                continue
            seen.add(key)
            if c.is_file():
                return c
        return None

    def _fallback_native(self) -> "AgentRegistry":
        spec = AgentSpec(id="native", default=True, workspace=None, cwds=[])
        self._agents = {"native": spec}
        self._default_id = "native"
        return self

    # ----------------------------------------------------------------- access
    def get(self, agent_id: str) -> AgentSpec | None:
        """normalize_agent_id(agent_id) 查 map,无返 None。"""
        return self._agents.get(normalize_agent_id(agent_id))

    def default(self) -> AgentSpec:
        """返回 _default_id 对应 spec;空 registry 返合成 native fallback。"""
        if self._default_id and self._default_id in self._agents:
            return self._agents[self._default_id]
        if self._agents:
            return next(iter(self._agents.values()))
        # 空 registry(理论不达:_fallback_native 已填,但防御)
        return AgentSpec(id="native", default=True, cwds=[])

    # -------------------------------------------------------------- resolvers
    def resolve_workspace(self, spec: AgentSpec, state_dir: str | None = None) -> Path:
        """workspace 显式→expanduser/resolve;None→stateDir/agents/<id>/workspace(决策 1)。

        返回前 mkdir(parents=True, exist_ok=True)。
        """
        if spec.workspace:
            ws = Path(spec.workspace).expanduser().resolve()
        else:
            ws = (
                Path(_state_dir(state_dir)) / "agents" / normalize_agent_id(spec.id) / "workspace"
            ).resolve()
        ws.mkdir(parents=True, exist_ok=True)
        return ws

    def resolve_cwd_scope(
        self, spec: AgentSpec, repo_root: str | None = None
    ) -> list[ResolvedCwd]:
        """cwd scope 解析(决策 4/5)。

        spec.cwds 非空 → 每 entry path 转 abs(repo_root/path),label None→Path(path).name。
        spec.cwds 空(含降级 native)→ 退化单 workspace cwd(default=True,workspace 作基准)。
        永不返空 list(下游 MultiCwdScopeCapability 清单空会崩)。
        """
        root = Path(repo_root or os.getenv("AO2_REPO_ROOT", str(Path.cwd()))).resolve()
        if spec.cwds:
            out: list[ResolvedCwd] = []
            for entry in spec.cwds:
                path_abs = (root / entry.path).resolve()
                label = entry.label or Path(entry.path).name
                out.append(ResolvedCwd(path_abs=str(path_abs), label=label, default=entry.default))
            return out
        # 单 cwd 退化:workspace 作基准
        ws_abs = self.resolve_workspace(spec)
        return [
            ResolvedCwd(
                path_abs=str(ws_abs),
                label=normalize_agent_id(spec.id),
                default=True,
            )
        ]
