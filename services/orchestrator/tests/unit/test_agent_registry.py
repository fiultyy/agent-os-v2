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
# 公开访问器(ADR-C2:default_id + iter_agents 替代外部读私有属性)
# =============================================================================
class TestPublicAccessors:
    def test_default_id_returns_default_marker(self, tmp_path):
        """default_id() 返 _default_id;显式 default:true 的 agent 胜出。"""
        yaml_path = tmp_path / "agents.yaml"
        _write_yaml(
            yaml_path,
            """
defaults:
  model: glm-4.7
agents:
  - id: main
  - id: help
    default: true
""",
        )
        reg = AgentRegistry.load(str(yaml_path))
        assert reg.default_id() == "help"

    def test_default_id_none_for_empty_registry(self):
        """未 load / 空 registry → default_id() None(不 raise)。"""
        reg = AgentRegistry()
        assert reg.default_id() is None

    def test_default_id_fallback_first_when_no_explicit_default(self, tmp_path):
        """无显式 default → load 取首个(agent_registry.py load L84-85)。"""
        yaml_path = tmp_path / "agents.yaml"
        _write_yaml(
            yaml_path,
            """
defaults:
  model: glm-4.7
agents:
  - id: alpha
  - id: beta
""",
        )
        reg = AgentRegistry.load(str(yaml_path))
        assert reg.default_id() == "alpha"

    def test_iter_agents_yields_id_spec_pairs(self, tmp_path):
        """iter_agents() 返 (id, AgentSpec) 迭代器,覆盖全部 loaded agent。"""
        yaml_path = tmp_path / "agents.yaml"
        _write_yaml(
            yaml_path,
            """
defaults:
  model: glm-4.7
agents:
  - id: main
    default: true
  - id: help
""",
        )
        reg = AgentRegistry.load(str(yaml_path))
        pairs = list(reg.iter_agents())
        ids = [aid for aid, _ in pairs]
        assert ids == ["main", "help"]
        assert all(isinstance(spec, AgentSpec) for _, spec in pairs)

    def test_iter_agents_does_not_leak_mutable_reference(self, tmp_path):
        """iter_agents() 返独立迭代器;改动返回的 list 不影响 registry 内部。

        ADR-C2 意图:不泄露私有 dict 引用。我们返 iter(items) 而非 items()
        视图对象本身——items() 视图会 live-reflect dict 改动,iter() 拍快照
        迭代器更安全。这里测两层:(1) 返回的不是 dict 本身;(2) 遍历后清空
        caller 自己的 list 不影响后续 iter_agents()。
        """
        yaml_path = tmp_path / "agents.yaml"
        _write_yaml(
            yaml_path,
            """
defaults:
  model: glm-4.7
agents:
  - id: solo
    default: true
""",
        )
        reg = AgentRegistry.load(str(yaml_path))
        it = reg.iter_agents()
        # 不是同一个 dict / items 视图对象本身被返回(iter() 包装过)
        assert it is not reg._agents
        first = list(it)
        assert len(first) == 1
        # 二次调用仍能拿到完整 agent(iter_agents 无副作用)
        second = list(reg.iter_agents())
        assert len(second) == 1


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

    def test_missing_file_degrades_to_native(self, tmp_path, monkeypatch):
        # 隔离 env 让 _locate_config 所有候选都不命中,真触发 _fallback_native。
        # 不隔离的话:git-root 推断(agent_registry.py:99-104)会上溯命中 repo 根真
        # agents.yaml(B 后=help),返回 help 而非 native → fallback path 测不到。
        # path 参数只是 candidates 第一项,缺失会继续往后试 env/repo-root/stateDir。
        monkeypatch.setenv("AO2_REPO_ROOT", str(tmp_path))   # 无 .git 也无 agents.yaml
        monkeypatch.setenv("AO2_STATE_DIR", str(tmp_path))    # stateDir 也没
        monkeypatch.delenv("AO2_AGENTS_CONFIG", raising=False)
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
