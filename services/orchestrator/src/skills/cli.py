"""
Agent OS Skill CLI (P3-B)

提供命令行 skill 管理工具。

用法：
    agent-os skill list
    agent-os skill show <name>
    agent-os skill enable <name>
    agent-os skill disable <name>
    agent-os skill reload
    agent-os skill search <query>

安装后可直接使用 `agent-os skill <command>`，或通过
`python -m agent_os_orchestrator.skills.cli` 调用。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional

# ---------------------------------------------------------------------------
# Bootstrap: locate catalog + config
# ---------------------------------------------------------------------------

# Default config file location
_DEFAULT_CONFIG_PATH = Path.home() / ".agent-os" / "skills.yaml"


def _get_catalog():
    """初始化并返回 SkillCatalog（已 reload）。"""
    from .skill_catalog import SkillCatalog
    from .skill_loader import SkillLoader

    catalog = SkillCatalog(loader=SkillLoader())
    catalog.reload()
    return catalog


def _get_config():
    """获取全局 SkillConfig 实例（用于 enable/disable）。"""
    from .skill_config import SkillConfig
    return SkillConfig(config_path=_DEFAULT_CONFIG_PATH)


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

# ANSI color codes
_GREEN = "\033[92m"
_RED = "\033[91m"
_YELLOW = "\033[93m"
_CYAN = "\033[96m"
_BOLD = "\033[1m"
_RESET = "\033[0m"

_NO_COLOR = not sys.stdout.isatty()


def _c(text: str, color: str) -> str:
    """应用颜色（仅 TTY 输出时有效）。"""
    if _NO_COLOR:
        return text
    return f"{color}{text}{_RESET}"


def _check_mark(enabled: bool) -> str:
    return _c("✓", _GREEN) if enabled else _c("✗", _RED)


def _fmt_col(value: str, width: int) -> str:
    """左对齐固定宽度列。"""
    return value.ljust(width)[:width]


# ---------------------------------------------------------------------------
# Command: list
# ---------------------------------------------------------------------------

def cmd_list(args: argparse.Namespace) -> int:
    """列出所有 skills（含状态）。"""
    catalog = _get_catalog()
    entries = catalog.list_all()

    if not entries:
        print("No skills found.")
        return 0

    # Sort by name
    entries = sorted(entries, key=lambda e: e.name)

    # Column widths
    name_w = max(len(e.name) for e in entries)
    name_w = max(name_w, 4)  # "NAME"
    ver_w = max(len(e.version) for e in entries)
    ver_w = max(ver_w, 7)  # "VERSION"
    src_w = max(len(e.source) for e in entries)
    src_w = max(src_w, 6)  # "SOURCE"

    # Header
    header = (
        _c(_fmt_col("NAME", name_w), _BOLD) + "  "
        + _c(_fmt_col("VERSION", ver_w), _BOLD) + "  "
        + _c(_fmt_col("SOURCE", src_w), _BOLD) + "  "
        + _c("ENABLED", _BOLD) + "  "
        + _c("DESCRIPTION", _BOLD)
    )
    print(header)
    print("-" * (name_w + ver_w + src_w + 30))

    for entry in entries:
        # Check enabled state via config
        try:
            cfg = _get_config()
            enabled = cfg.is_enabled(entry.name)
        except Exception:
            enabled = True  # assume enabled if config unavailable

        row = (
            _c(_fmt_col(entry.name, name_w), _CYAN) + "  "
            + _fmt_col(entry.version, ver_w) + "  "
            + _fmt_col(entry.source, src_w) + "  "
            + _fmt_col(_check_mark(enabled), 7) + "  "
            + entry.description
        )
        print(row)

    print(f"\n{len(entries)} skill(s) found")
    return 0


# ---------------------------------------------------------------------------
# Command: show
# ---------------------------------------------------------------------------

def cmd_show(args: argparse.Namespace) -> int:
    """显示 skill 详情。"""
    catalog = _get_catalog()
    entry = catalog.get(args.name)

    if entry is None:
        print(f"Error: skill '{args.name}' not found", file=sys.stderr)
        return 1

    try:
        cfg = _get_config()
        enabled = cfg.is_enabled(entry.name)
    except Exception:
        enabled = True

    print(f"{_c('Name:', _BOLD)}        {_c(entry.name, _CYAN)}")
    print(f"{_c('Version:', _BOLD)}     {entry.version}")
    print(f"{_c('Source:', _BOLD)}      {entry.source}")
    print(f"{_c('Enabled:', _BOLD)}     {_check_mark(enabled)}")
    print(f"{_c('Description:', _BOLD)} {entry.description}")
    print(f"{_c('Location:', _BOLD)}    {entry.location}")
    print(f"{_c('Base Dir:', _BOLD)}    {entry.base_dir}")

    if entry.requires.tools:
        print(f"{_c('Requires Tools:', _BOLD)} {', '.join(entry.requires.tools)}")
    if entry.requires.env:
        print(f"{_c('Requires Env:', _BOLD)}  {', '.join(entry.requires.env)}")

    print(f"{_c('Visible:', _BOLD)}     {entry.exposure.visible}")
    print(f"{_c('User Invocable:', _BOLD)} {entry.exposure.user_invocable}")

    # Show SKILL.md content if --content flag
    if getattr(args, "content", False):
        content = catalog.get_skill_content(entry.name)
        if content:
            print(f"\n{_c('SKILL.md:', _BOLD)}")
            print("-" * 40)
            print(content)
        else:
            print(f"\n{_c('SKILL.md:', _BOLD)} (not available)")

    return 0


# ---------------------------------------------------------------------------
# Command: enable
# ---------------------------------------------------------------------------

def cmd_enable(args: argparse.Namespace) -> int:
    """启用 skill。"""
    catalog = _get_catalog()
    entry = catalog.get(args.name)

    if entry is None:
        print(f"Error: skill '{args.name}' not found", file=sys.stderr)
        return 1

    try:
        cfg = _get_config()
        cfg.set_enabled(args.name, True)
        print(f"{_check_mark(True)}  Skill '{_c(args.name, _CYAN)}' enabled")
        return 0
    except Exception as e:
        print(f"Error: failed to enable skill '{args.name}': {e}", file=sys.stderr)
        return 1


# ---------------------------------------------------------------------------
# Command: disable
# ---------------------------------------------------------------------------

def cmd_disable(args: argparse.Namespace) -> int:
    """禁用 skill。"""
    catalog = _get_catalog()
    entry = catalog.get(args.name)

    if entry is None:
        print(f"Error: skill '{args.name}' not found", file=sys.stderr)
        return 1

    try:
        cfg = _get_config()
        cfg.set_enabled(args.name, False)
        print(f"{_check_mark(False)}  Skill '{_c(args.name, _CYAN)}' disabled")
        return 0
    except Exception as e:
        print(f"Error: failed to disable skill '{args.name}': {e}", file=sys.stderr)
        return 1


# ---------------------------------------------------------------------------
# Command: reload
# ---------------------------------------------------------------------------

def cmd_reload(args: argparse.Namespace) -> int:
    """强制 reload catalog。"""
    catalog = _get_catalog()
    count = len(catalog.list_all())
    print(f"Catalog reloaded: {count} skill(s) loaded (version {catalog.version})")
    return 0


# ---------------------------------------------------------------------------
# Command: search
# ---------------------------------------------------------------------------

def cmd_search(args: argparse.Namespace) -> int:
    """搜索 skills（名称 + 描述模糊匹配）。"""
    catalog = _get_catalog()
    query = args.query.lower()

    results = [
        entry
        for entry in catalog.list_all()
        if query in entry.name.lower() or query in entry.description.lower()
    ]

    if not results:
        print(f"No skills matching '{args.query}'")
        return 0

    results = sorted(results, key=lambda e: e.name)

    name_w = max(len(e.name) for e in results)
    name_w = max(name_w, 4)

    print(f"{_c(_fmt_col('NAME', name_w), _BOLD)}  {_c('DESCRIPTION', _BOLD)}")
    print("-" * (name_w + 30))
    for entry in results:
        print(f"{_c(_fmt_col(entry.name, name_w), _CYAN)}  {entry.description}")

    print(f"\n{len(results)} result(s)")
    return 0


# ---------------------------------------------------------------------------
# Skill subcommand parser
# ---------------------------------------------------------------------------

def _build_skill_subparser(subparsers: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    """向 subparsers 注册 'skill' 子命令组。"""
    skill_p = subparsers.add_parser("skill", help="Manage Agent OS skills")
    skill_sub = skill_p.add_subparsers(dest="skill_command", metavar="COMMAND")
    skill_sub.required = True

    # list
    list_p = skill_sub.add_parser("list", help="List all available skills")
    list_p.set_defaults(func=cmd_list)

    # show
    show_p = skill_sub.add_parser("show", help="Show skill details")
    show_p.add_argument("name", help="Skill name")
    show_p.add_argument("--content", action="store_true", help="Include SKILL.md content")
    show_p.set_defaults(func=cmd_show)

    # enable
    enable_p = skill_sub.add_parser("enable", help="Enable a skill")
    enable_p.add_argument("name", help="Skill name")
    enable_p.set_defaults(func=cmd_enable)

    # disable
    disable_p = skill_sub.add_parser("disable", help="Disable a skill")
    disable_p.add_argument("name", help="Skill name")
    disable_p.set_defaults(func=cmd_disable)

    # reload
    reload_p = skill_sub.add_parser("reload", help="Force reload skill catalog")
    reload_p.set_defaults(func=cmd_reload)

    # search
    search_p = skill_sub.add_parser("search", help="Search skills by name or description")
    search_p.add_argument("query", help="Search query")
    search_p.set_defaults(func=cmd_search)


def build_parser() -> argparse.ArgumentParser:
    """
    构建顶层 CLI argument parser。

    Usage::

        agent-os skill list
        agent-os skill show <name>
        agent-os skill enable <name>
        agent-os skill disable <name>
        agent-os skill reload
        agent-os skill search <query>
    """
    parser = argparse.ArgumentParser(
        prog="agent-os",
        description="Agent OS Management CLI",
    )
    subparsers = parser.add_subparsers(dest="group", metavar="GROUP")
    subparsers.required = True

    _build_skill_subparser(subparsers)

    return parser


def build_skill_parser() -> argparse.ArgumentParser:
    """
    构建独立的 skill CLI parser（standalone 模式，不需要 'agent-os skill' 前缀）。
    用于测试或直接调用。
    """
    parser = argparse.ArgumentParser(
        prog="agent-os skill",
        description="Agent OS Skill Management CLI",
    )
    subparsers = parser.add_subparsers(dest="skill_command", metavar="COMMAND")
    subparsers.required = True

    # list
    list_p = subparsers.add_parser("list", help="List all available skills")
    list_p.set_defaults(func=cmd_list)

    # show
    show_p = subparsers.add_parser("show", help="Show skill details")
    show_p.add_argument("name", help="Skill name")
    show_p.add_argument("--content", action="store_true", help="Include SKILL.md content")
    show_p.set_defaults(func=cmd_show)

    # enable
    enable_p = subparsers.add_parser("enable", help="Enable a skill")
    enable_p.add_argument("name", help="Skill name")
    enable_p.set_defaults(func=cmd_enable)

    # disable
    disable_p = subparsers.add_parser("disable", help="Disable a skill")
    disable_p.add_argument("name", help="Skill name")
    disable_p.set_defaults(func=cmd_disable)

    # reload
    reload_p = subparsers.add_parser("reload", help="Force reload skill catalog")
    reload_p.set_defaults(func=cmd_reload)

    # search
    search_p = subparsers.add_parser("search", help="Search skills by name or description")
    search_p.add_argument("query", help="Search query")
    search_p.set_defaults(func=cmd_search)

    return parser


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point。返回退出码。"""
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


def skill_cli_main() -> None:
    """pyproject.toml 入口点函数（无返回值）。"""
    sys.exit(main())


if __name__ == "__main__":
    sys.exit(main())
