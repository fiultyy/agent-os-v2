"""Unit tests for AgentRegistry (P0 配置地基).

覆盖验收:正常 load/resolve、坏 YAML 降级、cwds=[] 单 cwd 退化、缺失文件降级。
sync 测试,无需 loop wrapper(纯 schema + 路径解析,无 async)。
"""
from pathlib import Path

from agent.agent_registry import AgentRegistry, ResolvedCwd
from agent.agent_spec import AgentSpec


def _write_yaml(p: Path, body: str) -> Path:
    p.write_text(body, encoding="utf-8")
    return p


# =============================================================================
# 正常 load + resolve
# =============================================================================
class TestLoadAndResolve:
    def test_load_get_and_resolve_cwd_scope(self, tmp_path):
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        (repo_root / "services").mkdir()
        (repo_root / "apps").mkdir()
        yaml_path = tmp_path / "agents.yaml"
        _write_yaml(
            yaml_path,
            """
defaults:
  model: glm-4.7
agents:
  - id: native
    default: true
    model: glm-4.7
    cwds:
      - path: services/orchestrator
        label: orchestrator
        default: true
      - path: apps/tui-rs
        label: tui-rs
""",
        )
        reg = AgentRegistry.load(str(yaml_path))

        spec = reg.get("native")
        assert spec is not None, "registry.get('native') should resolve"
        assert reg.default().id == "native"

        scope = reg.resolve_cwd_scope(spec, repo_root=str(repo_root))
        assert len(scope) == 2, "cwds 2 条 → 2 ResolvedCwd"
        assert all(isinstance(e, ResolvedCwd) for e in scope)
        # path_abs 绝对且以 repo_root 开头
        for e in scope:
            assert Path(e.path_abs).is_absolute()
            assert str(e.path_abs).startswith(str(repo_root))
        # default cwd 正是 default=True 那条
        defaults = [e for e in scope if e.default]
        assert len(defaults) == 1
        assert defaults[0].label == "orchestrator"


# =============================================================================
# 坏 YAML → 降级单 native(不 raise)
# =============================================================================
class TestFallbackDegrade:
    def test_bad_yaml_degrades_to_native(self, tmp_path):
        yaml_path = tmp_path / "agents.yaml"
        # 截断的坏 YAML(未闭合 quote)
        _write_yaml(yaml_path, "defaults:\n  model: 'unterminated\n  agents: [")
        reg = AgentRegistry.load(str(yaml_path))  # 不 raise
        assert reg.default().id == "native"
        assert reg.get("native") is not None

    def test_missing_file_degrades_to_native(self, tmp_path):
        nope = tmp_path / "nope.yaml"
        reg = AgentRegistry.load(str(nope))
        assert reg.default().id == "native"
        assert reg.get("native") is not None


# =============================================================================
# cwds=[] → 单 cwd 退化(workspace 作基准)
# =============================================================================
class TestCwdEmptyDegenerate:
    def test_empty_cwds_single_workspace_cwd(self, tmp_path):
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        yaml_path = tmp_path / "agents.yaml"
        _write_yaml(
            yaml_path,
            """
defaults:
  model: glm-4.7
agents:
  - id: solo
    default: true
    cwds: []
""",
        )
        reg = AgentRegistry.load(str(yaml_path))
        spec = reg.get("solo")
        assert spec is not None

        scope = reg.resolve_cwd_scope(spec, repo_root=str(repo_root))
        assert len(scope) == 1, "cwds=[] → 单 cwd 退化,不返空 list"
        entry = scope[0]
        assert entry.default is True
        ws = reg.resolve_workspace(spec)
        assert entry.path_abs == str(ws), "退化 cwd path_abs == resolve_workspace(spec)"
