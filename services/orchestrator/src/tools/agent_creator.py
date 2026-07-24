"""create_agent tool (ADR-3) — queen 写文件能力。

把一个 agent 配置原子落盘到 agents.yaml + workspace/SOUL.md + workspace/AGENTS.md
(+ 可选 skill)。灾难底线:同名 agent 拒(不覆盖现有);agents.yaml 用临时文件 +
os.replace 原子替换,写错不破坏现有配置。

注册名 **create_agent**(无 v2_ 前缀,RK11)— ToolBridgeCapability 运行时自动
`.prefixed("v2")`,模型可见名 = v2_create_agent。若 register 带 v2_ 前缀会
叠成 v2_v2_create_agent。
"""
from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# JSON schema 暴露给 ToolBridgeCapability → 模型 function-calling。
CREATE_AGENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "id": {
            "type": "string",
            "pattern": r"^[a-z0-9][a-z0-9_-]{0,63}$",
            "description": (
                "Agent id(路由 key)。必须小写字母/数字开头,仅含 [a-z0-9_-],"
                "长度 1–64。合法则原样保留,非法会被拒绝。"
            ),
        },
        "name": {"type": "string", "description": "显示名(TUI / A2A card 投影用)"},
        "model": {"type": "string", "description": "模型 id;缺则回退 agents.yaml defaults.model"},
        "workspace": {
            "type": "string",
            "description": (
                "身份目录(绝对路径,repo 内 trackable)。orchestrator 启动 "
                "cwd=services/orchestrator,相对路径会拼错 → 必须绝对路径。"
            ),
        },
        "skills": {
            "type": "array", "items": {"type": "string"},
            "description": "defer skill 列表(按需 load_capability 载入)",
        },
        "instructions": {
            "type": "string",
            "description": "短指令(进 stable prefix,跨轮 cache)。2–4 句核心定位。",
        },
        "cwds": {
            "type": "array",
            "description": "操作 cwd scope;空 → 单 workspace cwd 退化",
            "items": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "相对 repo root"},
                    "label": {"type": "string"},
                    "default": {"type": "boolean"},
                },
                "required": ["path"],
            },
        },
        "soul_md": {"type": "string", "description": "workspace/SOUL.md 内容(L0 人设)"},
        "agents_md": {"type": "string", "description": "workspace/AGENTS.md 内容(L1 身份 + L2 规则)"},
        "effort": {
            "type": "string", "enum": ["low", "medium", "high", "xhigh", "max"],
            "description": "推理努力档;缺省 medium",
        },
        "default": {
            "type": "boolean",
            "description": "是否设为默认 agent(谨慎:已有 default 会被此条覆盖触发 schema 冲突)",
        },
        "skill_name": {
            "type": "string",
            "description": "(可选)若提供,创建 services/skills/<skill_name>/SKILL.md",
        },
        "skill_md": {
            "type": "string",
            "description": "(可选,配合 skill_name)SKILL.md 内容",
        },
    },
    "required": ["id"],
}


def create_agent(
    id: str,
    name: str | None = None,
    model: str | None = None,
    workspace: str | None = None,
    skills: list[str] | None = None,
    instructions: str | None = None,
    cwds: list[dict[str, Any]] | None = None,
    soul_md: str | None = None,
    agents_md: str | None = None,
    effort: str = "medium",
    default: bool = False,
    skill_name: str | None = None,
    skill_md: str | None = None,
    *,
    config_path: str | None = None,
) -> dict[str, Any]:
    """原子创建 AO2 agent(append agents.yaml + workspace 文件 + 可选 skill)。

    Args:
        id: agent id(必需,合法 pattern ^[a-z0-9][a-z0-9_-]{0,63}$)。
        config_path: 显式 agents.yaml 路径(测试注入);None → 复用
            AgentRegistry._locate_config() 找规范位置(repo 根优先)。

    Returns:
        ``{ok, agent_id, workspace, files_written, note}``。
        失败 raise(ToolExecutor 转 status=error)。

    灾难底线:同名 agent 拒(不覆盖);agents.yaml 原子写(临时文件 + os.replace)。
    """
    # 延迟 import 避开模块加载顺序(engine 注册时 src.agent 已可用)。
    from src.agent.agent_spec import (
        AgentSpec, AgentsConfig, CwdEntry, Defaults, ToolPolicy, normalize_agent_id,
    )

    # ── 1. id 合法 + 唯一 ──────────────────────────────────────────────
    norm_id = normalize_agent_id(id)
    if norm_id != id:
        # 非法 id:normalize 改了 → 拒绝(让调用方修 id,不静默改)
        raise ValueError(
            f"invalid agent id {id!r}: must match ^[a-z0-9][a-z0-9_-]{{0,63}}$ "
            f"(normalized to {norm_id!r})"
        )

    # ── 2. 定位 agents.yaml + 读现有 ────────────────────────────────────
    target_path = _resolve_config_path(config_path)
    if target_path is None or not target_path.is_file():
        raise FileNotFoundError(
            f"agents.yaml not found (looked: {config_path or 'AgentRegistry._locate_config'}); "
            "cannot append to non-existent config"
        )

    import yaml  # pyyaml 是 AO2 必依赖(agent_registry.load 用)

    with open(target_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"agents.yaml root must be a mapping, got {type(raw).__name__}")

    existing_ids = {a.get("id") for a in raw.get("agents", []) if isinstance(a, dict)}
    if norm_id in existing_ids:
        # 灾难底线:同名拒(不覆盖现有 agent)
        raise ValueError(
            f"agent id {norm_id!r} already exists in {target_path}; "
            "refusing to overwrite (edit agents.yaml manually to modify)"
        )

    # ── 3. 组新条目 + schema 校验(整份,不只新条目) ─────────────────────
    new_entry: dict[str, Any] = {"id": norm_id, "default": default}
    if name is not None:
        new_entry["name"] = name
    if model is not None:
        new_entry["model"] = model
    if workspace is not None:
        new_entry["workspace"] = workspace
    if skills:
        new_entry["skills"] = list(skills)
    if instructions is not None:
        new_entry["instructions"] = instructions
    if cwds:
        new_entry["cwds"] = list(cwds)
    if effort != "medium":
        new_entry["effort"] = effort

    merged = {
        "defaults": raw.get("defaults") or {"model": None},
        "agents": list(raw.get("agents", [])) + [new_entry],
    }
    # 整份校验:catch 重复 default / 重复 id / cwd default 冲突等。
    cfg = AgentsConfig.model_validate(merged)
    new_spec = next(a for a in cfg.agents if a.id == norm_id)

    # ── 4. 原子写 agents.yaml(临时文件 + os.replace) ───────────────────
    _atomic_write_yaml(target_path, merged)

    # ── 5. mkdir workspace + write SOUL.md / AGENTS.md ──────────────────
    files_written: list[str] = []
    ws_path = _resolve_workspace(new_spec, target_path)
    ws_path.mkdir(parents=True, exist_ok=True)

    if soul_md is not None:
        soul_file = ws_path / "SOUL.md"
        _atomic_write_text(soul_file, soul_md)
        files_written.append(str(soul_file))
    if agents_md is not None:
        agents_file = ws_path / "AGENTS.md"
        _atomic_write_text(agents_file, agents_md)
        files_written.append(str(agents_file))

    # ── 6. (可选)创建 skill ─────────────────────────────────────────────
    if skill_name is not None:
        if skill_md is None:
            raise ValueError("skill_name provided without skill_md content")
        # skill 目录相对 repo root(services/skills/<name>/SKILL.md)
        repo_root = _repo_root(target_path)
        skill_file = repo_root / "services" / "skills" / skill_name / "SKILL.md"
        skill_file.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(skill_file, skill_md)
        files_written.append(str(skill_file))

    logger.info(
        "create_agent: id=%s workspace=%s files=%d (agents.yaml appended atomically)",
        norm_id, ws_path, len(files_written),
    )

    return {
        "ok": True,
        "agent_id": norm_id,
        "workspace": str(ws_path),
        "files_written": files_written,
        "note": "重启 orche 生效(registry 启动加载 agents.yaml,不做热加载)",
    }


# ── 内部辅助 ────────────────────────────────────────────────────────────

def _resolve_config_path(config_path: str | None) -> Path | None:
    """定位 agents.yaml:显式参数 > AO2_AGENTS_CONFIG env > AgentRegistry._locate_config。"""
    if config_path:
        return Path(config_path).expanduser()
    env_cfg = os.getenv("AO2_AGENTS_CONFIG")
    if env_cfg:
        return Path(env_cfg).expanduser()
    # 复用 registry 的规范定位逻辑(repo 根优先)。
    from src.agent.agent_registry import AgentRegistry
    return AgentRegistry._locate_config(None)


def _resolve_workspace(spec: Any, config_path: Path) -> Path:
    """resolve workspace 绝对路径(spec.workspace 绝对 / None → stateDir 派生)。

    对齐 agent_registry._resolve_workspace 语义:绝对路径原样,无 workspace 时
    落 stateDir/agents/<id>/workspace。本工具要求调用方传 workspace(queen
    产物模板明确),此处 None 分支仅兜底。
    """
    ws = getattr(spec, "workspace", None)
    if ws:
        p = Path(ws).expanduser()
        return p if p.is_absolute() else (config_path.parent / p).resolve()
    # 兜底:stateDir 派生。
    state_dir = os.path.expanduser(os.getenv("AO2_STATE_DIR", "~/.agent-os"))
    return Path(state_dir) / "agents" / spec.id / "workspace"


def _repo_root(config_path: Path) -> Path:
    """从 agents.yaml 路径推 repo root(上溯找 .git)。"""
    d = config_path.parent
    for cand in [d, *d.parents]:
        if (cand / ".git").exists():
            return cand
    return d


def _atomic_write_yaml(target: Path, data: dict[str, Any]) -> None:
    """原子写 YAML:同目录临时文件 + os.replace(POSIX 原子 rename)。"""
    import yaml
    tmp_fd, tmp_path = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent),
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            yaml.safe_dump(
                data, f, default_flow_style=False, allow_unicode=True,
                sort_keys=False,
            )
        os.replace(tmp_path, target)
    except Exception:
        # 清理临时文件(不破坏现有 agents.yaml)。
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _atomic_write_text(target: Path, content: str) -> None:
    """原子写文本:同目录临时文件 + os.replace。"""
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_path = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent),
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_path, target)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
