"""P6 SkillCapability 单测:defer(defer_loading=True,glm 调 load_capability e2e 验证)+ body(去 frontmatter)+ requires.env 校验 + factory。

用 tmp_path 写 SKILL.md + SkillLoader(user_dir=tmp_path)scan + make_skill_capabilities。
"""

from __future__ import annotations

import asyncio

import pytest
from pydantic_ai import ModelRetry

from src.harness.capabilities import SkillCapability, make_skill_capabilities
from src.skills.skill_loader import SkillLoader


class _Call:
    tool_name = "x"


class _TD:
    name = "x"


def _loader(tmp_path) -> SkillLoader:
    # builtin_dir 显式 = tmp_path,避免默认推断 services/skills/ 扫到
    # ao2-architecture builtin 泄漏污染 fixture(三层都指 tmp,去重后只 my-skill)。
    return SkillLoader(user_dir=tmp_path, project_dir=tmp_path, builtin_dir=tmp_path)


def test_skill_capability_body_strips_frontmatter(tmp_path) -> None:
    skill_dir = tmp_path / "my-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: my-skill\ndescription: test skill\n---\nDo the thing step by step.\n",
        encoding="utf-8",
    )
    cap = make_skill_capabilities(_loader(tmp_path))[0]
    assert cap.id == "my-skill"
    assert cap.description == "test skill"
    assert cap.defer_loading is True  # defer:glm 调 load_capability(e2e 3 场景验证),渐进按需加载
    body = cap.get_instructions()
    assert "Do the thing step by step." in body
    assert "name: my-skill" not in body          # frontmatter 已去


def test_skill_capability_requires_env_blocks(tmp_path, monkeypatch) -> None:
    skill_dir = tmp_path / "needs-env"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: needs-env\ndescription: x\nrequires:\n  env: [MY_API_KEY]\n---\nbody\n",
        encoding="utf-8",
    )
    cap = make_skill_capabilities(_loader(tmp_path))[0]
    monkeypatch.delenv("MY_API_KEY", raising=False)
    with pytest.raises(ModelRetry):
        asyncio.run(cap.before_tool_execute(
            ctx=None, call=_Call(), tool_def=_TD(), args={}))


def test_skill_capability_requires_env_satisfied(tmp_path, monkeypatch) -> None:
    skill_dir = tmp_path / "ok-env"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: ok-env\ndescription: x\nrequires:\n  env: [MY_API_KEY]\n---\nbody\n",
        encoding="utf-8",
    )
    cap = make_skill_capabilities(_loader(tmp_path))[0]
    monkeypatch.setenv("MY_API_KEY", "set")
    out = asyncio.run(cap.before_tool_execute(
        ctx=None, call=_Call(), tool_def=_TD(), args={"a": 1}))
    assert out == {"a": 1}                        # env 满足 → 放行


def test_make_skill_capabilities_factory_dedups(tmp_path) -> None:
    # 两个 skill
    for name in ("skill-a", "skill-b"):
        d = tmp_path / name
        d.mkdir()
        (d / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: {name} desc\n---\nbody {name}\n",
            encoding="utf-8",
        )
    caps = make_skill_capabilities(_loader(tmp_path))
    assert {c.id for c in caps} == {"skill-a", "skill-b"}
