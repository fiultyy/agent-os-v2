#!/usr/bin/env python3
"""doc_check.py — 规划 §7 文档数字 CI 回写守门(解 kg 审核 HIGH 根因:文档漂移)。

三个子命令:
  collect   pytest --co(只收集不执行,避免环境性 flake)解析 "N tests collected"
            + 捕获 collection error 行(canvas e2e requests 缺 → tolerant 报告,不 crash)
  islands   grep "# NOT-WIRED" 标注 → defer 孤岛 allowlist(已知孤岛,非新告警)
  verify    读规划文档 "<!-- DOC-CHECK: tests=<N> -->" 锚点,与 collect 实测对比,漂移 exit 1

锚点 = 期望值(文档作者维护),CI 只校验不修改。
无子命令时默认 collect + verify。

设计原则:
- 纯标准库 + pytest(子进程调用,不 import memory/不触发 on_turn_end/emit)
- tolerant 解析:pytest 退出码 2(collection error)仍提取 collected 数
- 幂等:同环境同 HEAD 多次跑输出一致(collect-only,无随机/网络)
- 红线:零触碰 services/(本脚本只读规划文档 + 调 pytest --co)

用法:
  python scripts/doc_check.py                    # 默认 collect + verify
  python scripts/doc_check.py collect
  python scripts/doc_check.py verify
  python scripts/doc_check.py islands
  python scripts/doc_check.py collect verify islands
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional

# --- 路径常量(相对仓库根,脚本可从任意 cwd 调用) ---
REPO_ROOT = Path(__file__).resolve().parent.parent
ORCH_DIR = REPO_ROOT / "services" / "orchestrator"
# 锚点校验的规划文档(锚点放置位置)
DOC_FILES = [
    REPO_ROOT / "docs" / "mvp-iteration-roadmap.md",
    REPO_ROOT / "docs" / "multi-agent-poweron-roadmap.md",
]
# NOT-WIRED 扫描根
ISLAND_ROOTS = [ORCH_DIR / "src", REPO_ROOT / "tests"]
# canvas e2e collection error(requests 缺)为已知 noise —— 收集并报告,不静默不 crash
CANVAS_E2E = "tests/e2e/test_canvas_e2e.py"

# 锚点正则:<!-- DOC-CHECK: tests=706 --> (允许注释后缀)
ANCHOR_RE = re.compile(r'<!--\s*DOC-CHECK:\s*tests=(\d+)\s*(?:,\s*desc="([^"]*)")?\s*-->')
# pytest collect-only 汇总行:"706 tests collected" 或 "706 tests collected, 1 error in 0.89s"
COLLECTED_RE = re.compile(r"(\d+)\s+tests?\s+collected")
# collection error 行(pytest 两种格式):
#   详细标题:  "________________ ERROR collecting tests/e2e/test_canvas_e2e.py _______________"
#   摘要行:    "ERROR tests/e2e/test_canvas_e2e.py"
ERROR_COLLECT_RE = re.compile(r"ERROR\s+(?:collecting\s+)?(\S+?\.py)\b")


def _resolve_python() -> str:
    """解析跑 pytest 的 python 解释器。

    优先级:
      1. env DOC_CHECK_PYTHON(CI 显式指定 setup-python 的 python)
      2. services/orchestrator/.venv/bin/python(本地开发 venv,带全量依赖)
      3. sys.executable(系统 python 兜底,需 pytest 可 import)
    """
    env_py = os.environ.get("DOC_CHECK_PYTHON")
    if env_py:
        # 相对路径相对仓库根解析(subprocess cwd 会切到 orchestrator 目录)
        env_path = Path(env_py)
        if not env_path.is_absolute():
            env_path = REPO_ROOT / env_path
        if env_path.exists():
            return str(env_path)
        # CI 中 "python" 这类纯命令名 → 直接用,让 PATH 解析
        if "/" not in env_py and "\\" not in env_py:
            return env_py
    venv_py = ORCH_DIR / ".venv" / "bin" / "python"
    if venv_py.exists():
        return str(venv_py)
    return sys.executable


def _run_pytest_collect() -> tuple[str, int]:
    """跑 pytest --co(不执行),返回 (stdout+stderr 合并文本, 进程退出码)。

    --ignore canvas e2e 备选方案见 collect();此处先跑全集捕获 error 行。
    退出码 2(collection error)属正常,由调用方解析文本判断。
    """
    cmd = [
        _resolve_python(),
        "-m", "pytest",
        "tests/",
        "-q", "--co",
    ]
    proc = subprocess.run(
        cmd,
        cwd=str(ORCH_DIR),
        capture_output=True,
        text=True,
        timeout=120,
    )
    return proc.stdout + proc.stderr, proc.returncode


def cmd_collect() -> dict:
    """collect 子命令:pytest --co 解析 collected 数 + collection errors。

    返回 {collected: int, collection_errors: [str], known_noise: bool}。
    tolerant:collection error 不 crash,捕获并列为已知 noise(canvas e2e)。
    """
    text, rc = _run_pytest_collect()

    collected = 0
    m = COLLECTED_RE.search(text)
    if m:
        collected = int(m.group(1))
    else:
        # 无 collected 行 = pytest 自身崩/环境问题,属真异常
        print(f"[collect] FATAL: 未在 pytest 输出中找到 'N tests collected'(rc={rc})", file=sys.stderr)
        print("---- pytest tail ----", file=sys.stderr)
        print(text[-800:], file=sys.stderr)
        sys.exit(3)

    # 收集 collection error 源文件
    errors = list(set(ERROR_COLLECT_RE.findall(text)))
    # 已知 noise:canvas e2e requests 缺
    known = [e for e in errors if e.rstrip("/").endswith("test_canvas_e2e.py")]
    new_errors = [e for e in errors if e not in known]

    result = {
        "collected": collected,
        "collection_errors": errors,
        "known_noise": known,
        "new_collection_errors": new_errors,
        "pytest_rc": rc,
    }
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return result


def _scan_anchors() -> list[dict]:
    """扫描 DOC_FILES 中的 DOC-CHECK 锚点,返回 [{file, line, tests, raw}]。"""
    anchors = []
    for doc in DOC_FILES:
        if not doc.exists():
            continue
        for i, line in enumerate(doc.read_text(encoding="utf-8").splitlines(), 1):
            m = ANCHOR_RE.search(line)
            if m:
                anchors.append({
                    "file": str(doc.relative_to(REPO_ROOT)),
                    "line": i,
                    "tests": int(m.group(1)),
                    "raw": line.strip(),
                })
    return anchors


def cmd_verify() -> int:
    """verify 子命令:读锚点 vs collect 实测,漂移 exit 1。

    多锚点不一致(同一文档作者维护失误)也 exit 1。
    """
    # 先 collect
    text, rc = _run_pytest_collect()
    actual_m = COLLECTED_RE.search(text)
    if not actual_m:
        print("[verify] FATAL: 无法 collect(见 collect 子命令输出)", file=sys.stderr)
        return 3
    actual = int(actual_m.group(1))

    anchors = _scan_anchors()
    if not anchors:
        print("[verify] FAIL: 规划文档中未找到任何 <!-- DOC-CHECK: tests=N --> 锚点", file=sys.stderr)
        print("         请在 docs/mvp-iteration-roadmap.md / multi-agent-poweron-roadmap.md 添加锚点", file=sys.stderr)
        return 1

    exit_code = 0
    expected_set = {a["tests"] for a in anchors}

    # 锚点之间不一致
    if len(expected_set) > 1:
        print(f"[verify] FAIL: 锚点期望值不一致 —— 多个文档/多锚点写了不同的 tests 数", file=sys.stderr)
        for a in anchors:
            print(f"  {a['file']}:{a['line']}  tests={a['tests']}", file=sys.stderr)
        exit_code = 1

    expected = next(iter(expected_set))
    print(f"[verify] 锚点期望 tests={expected}  |  collect 实测 collected={actual}")

    if actual != expected:
        print(f"[verify] FAIL ❌ 漂移:文档锚点 {expected} ≠ 实测 {actual}(delta={actual - expected:+d})")
        print("         → 修复:更新 docs/* 锚点 tests=N 为实测值(若测试数有意变化),")
        print("           或补回丢失的测试(若为意外回归)。锚点=collect 数(稳定可重现)。")
        for a in anchors:
            print(f"  - {a['file']}:{a['line']}  (当前 tests={a['tests']})")
        return 1

    print("[verify] OK ✅ 锚点与实测一致(零漂移)")
    return exit_code


def cmd_islands() -> int:
    """islands 子命令:grep "# NOT-WIRED" 标注 → defer 孤岛 allowlist。

    NOT-WIRED = d1e64ef 埋的 defer 孤岛标注(SandboxExecutor/ScopeManager/
    ConcurrencyController/canvas API 等)。识别为"已知孤岛",非新告警。
    若某 NOT-WIRED 孤岛后续通电,应同步移除标注 —— 消失即进步。
    """
    found = []
    for root in ISLAND_ROOTS:
        if not root.exists():
            continue
        for py in root.rglob("*.py"):
            try:
                text = py.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if "NOT-WIRED" in line:
                    # 取冒号后的简短描述(中文也按 : 切)
                    desc = line.split("NOT-WIRED", 1)[1].lstrip(": ").strip()[:90]
                    found.append({
                        "file": str(py.relative_to(REPO_ROOT)),
                        "line": i,
                        "desc": desc or "(deferred 孤岛)",
                    })

    # 去重同文件(同孤岛多标注,如 conditional_spawner.py 有 2 条)
    by_file: dict[str, list[dict]] = {}
    for f in found:
        by_file.setdefault(f["file"], []).append(f)

    print(f"[islands] NOT-WIRED defer 孤岛 allowlist —— {len(found)} 处标注 / {len(by_file)} 个文件")
    print("(NOT-WIRED = 已知 defer 孤岛,非新告警;通电后应移除标注,消失即进步)")
    print()
    for fname in sorted(by_file):
        entries = by_file[fname]
        if len(entries) == 1:
            e = entries[0]
            print(f"  {fname}:{e['line']}  → {e['desc']}")
        else:
            print(f"  {fname}  ({len(entries)} 处标注)")
            for e in entries:
                print(f"    :{e['line']}  → {e['desc']}")
    print()
    print(f"[islands] 合计 {len(by_file)} 个 defer 孤岛文件 / {len(found)} 处标注")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="doc_check.py",
        description="规划 §7 文档数字 CI 回写守门(解 kg 审核 HIGH 根因:文档漂移)",
    )
    parser.add_argument(
        "commands",
        nargs="*",
        choices=["collect", "verify", "islands"],
        help="要执行的子命令(可多选,空则默认 collect + verify)",
    )
    args = parser.parse_args(argv)
    commands = args.commands or ["collect", "verify"]

    rc = 0
    for cmd in commands:
        print(f"\n{'=' * 60}\n>>> {cmd}\n{'=' * 60}")
        if cmd == "collect":
            cmd_collect()
        elif cmd == "verify":
            r = cmd_verify()
            rc = rc or r
        elif cmd == "islands":
            cmd_islands()
    return rc


if __name__ == "__main__":
    sys.exit(main())
