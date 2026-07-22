"""单测 tools/cwd_scope.py(T4)。

验收标准:
- _resolve('/etc/hosts') 绝对路径直接 resolve(不依赖 _active_cwd)。
- set _active_cwd 到 tmp_path/a 后 _resolve('x.txt')==tmp_path/a/x.txt resolve。
- 不 set 时 _resolve('x')==Path.cwd()/'x'。
- set/get_session_active_cwd round-trip:get('none-k') is None。
- _active_cwd.set(None) 后 _resolve('rel') 回退 Path.cwd()。
"""

from pathlib import Path

from tools.cwd_scope import (
    _SESSION_CWD,
    _active_cwd,
    _resolve,
    get_session_active_cwd,
    set_session_active_cwd,
)


def test_resolve_absolute_passthrough(tmp_path):
    # 绝对路径不依赖 _active_cwd(向后兼容:行为不变)。
    _active_cwd.set(Path("/nonexistent/base"))  # 故意设错基准,证明绝对路径不走它。
    try:
        got = _resolve(str(tmp_path / "a.txt"))
    finally:
        _active_cwd.set(None)
    assert got == (tmp_path / "a.txt").resolve()


def test_resolve_relative_uses_active_cwd(tmp_path):
    base = tmp_path / "a"
    _active_cwd.set(base)
    try:
        assert _resolve("x.txt") == (base / "x.txt").resolve()
    finally:
        _active_cwd.set(None)


def test_resolve_relative_falls_back_to_process_cwd():
    # 未 set contextvar → 等价 Path.cwd()/'x'(默认基准退化等价进程 cwd)。
    _active_cwd.set(None)
    assert _resolve("x") == (Path.cwd() / "x").resolve()


def test_resolve_explicit_none_active_cwd_falls_back_to_process_cwd(tmp_path):
    # 决策 4 退化:_active_cwd.set(None) 后相对路径回退 Path.cwd()。
    _active_cwd.set(None)
    try:
        assert _resolve("rel") == (Path.cwd() / "rel").resolve()
    finally:
        _active_cwd.set(None)


def test_session_active_cwd_round_trip():
    # round-trip:set('k','/tmp/a');assert get('k')=='/tmp/a';assert get('none-k') is None。
    # 用唯一 key 避免跨测试污染(dict 模块级共享)。
    k = "claude-code:test-cwd-scope-roundtrip"
    prev = _SESSION_CWD.get(k)
    try:
        set_session_active_cwd(k, "/tmp/a")
        assert get_session_active_cwd(k) == "/tmp/a"
        assert get_session_active_cwd("none-k-" + k) is None
    finally:
        if prev is None:
            _SESSION_CWD.pop(k, None)
        else:
            _SESSION_CWD[k] = prev
        _active_cwd.set(None)
