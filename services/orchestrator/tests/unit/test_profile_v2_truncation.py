"""V2 用户指令文件清单 + per-file 截断(ADR L26)哨兵测。

防回归:文件集完整性 + per-file 截断保前段 + 缺文件静默跳过 + 阈值内不截断。
"""

from __future__ import annotations

import src.agent.profile_registry as pr_mod
from src.agent.profile_registry import ProfileRegistry


def _all_sources(profile) -> set[str]:
    return {p.source for layer in range(5) for p in profile.get_layer(layer)}


def test_truncates_long_file_preserving_head(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(pr_mod, "_MAX_CHARS", 100)
    (tmp_path / "MEMORY.md").write_text("X" * 500, encoding="utf-8")
    profile = ProfileRegistry().load_from_files("a", str(tmp_path))
    l3 = profile.get_layer(3)
    assert len(l3) == 1
    assert "[truncated" in l3[0].content
    assert l3[0].content.startswith("X" * 100)  # 前段核心保留


def test_loads_all_stable_files(tmp_path) -> None:
    (tmp_path / "SOUL.md").write_text("soul-content", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("# Role\nidentity\n\n## Rules\nguidelines", encoding="utf-8")
    (tmp_path / "MEMORY.md").write_text("mem", encoding="utf-8")
    (tmp_path / "TOOLS.md").write_text("tls", encoding="utf-8")
    (tmp_path / "BOOTSTRAP.md").write_text("boot", encoding="utf-8")
    sources = _all_sources(ProfileRegistry().load_from_files("a", str(tmp_path)))
    for s in ("soul_md", "agents_md_identity", "agents_md_guidelines",
              "memory_md", "tools_md", "bootstrap_md"):
        assert s in sources, f"缺 {s}"


def test_missing_files_silently_skipped(tmp_path) -> None:
    (tmp_path / "AGENTS.md").write_text("# Role\nonly identity", encoding="utf-8")
    profile = ProfileRegistry().load_from_files("a", str(tmp_path))
    assert len(profile.get_layer(0)) == 0  # 无 SOUL
    assert len(profile.get_layer(3)) == 0  # 无 MEMORY
    assert len(profile.get_layer(4)) == 0  # 无 TOOLS/BOOTSTRAP
    assert len(profile.get_layer(1)) == 1  # AGENTS identity


def test_no_truncation_under_limit(tmp_path) -> None:
    (tmp_path / "MEMORY.md").write_text("short content", encoding="utf-8")
    profile = ProfileRegistry().load_from_files("a", str(tmp_path))
    assert "[truncated" not in profile.get_layer(3)[0].content


def test_agents_split_not_truncated_by_default(tmp_path) -> None:
    """AGENTS.md _split L1+L2 默认阈值内不截断(回归保护)。"""
    (tmp_path / "AGENTS.md").write_text("# Role\nid\n\n## Rules\nrule1\n\n## More\nrule2", encoding="utf-8")
    profile = ProfileRegistry().load_from_files("a", str(tmp_path))
    assert len(profile.get_layer(1)) == 1  # identity
    assert len(profile.get_layer(2)) == 1  # guidelines(Rules + More 合并)
