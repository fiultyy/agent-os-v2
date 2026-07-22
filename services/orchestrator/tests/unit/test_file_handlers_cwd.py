"""单测 T6:文件 handler 经 cwd_scope._resolve 解析(P1 多 cwd)。

验收标准:
- file_read: set _active_cwd=tmp_path/a 后 file_read('x.txt') 命中 tmp_path/a/x.txt;
  绝对路径 file_read(str(tmp_path/a/x.txt)) 同样成功(绝对路径不变)。
- code_read/code_write/code_search: 相对路径经 _active_cwd 解析到正确目录。
- 不 set _active_cwd 时相对路径走 Path.cwd()(默认退化,与改造前一致)。

约定:pythonpath=["src"],故 import 根为 `skills.*` / `tools.*`。
"""

from pathlib import Path

# 必须与 handler 的 import 路径一致(skills/*.py 用 `from src.tools.cwd_scope`),
# 否则 ContextVar 双实例(同名不同模块)致 set/get 失联。
from src.tools.cwd_scope import _active_cwd
from src.skills.primitive.file_tool import file_read, file_write
from src.skills.code.read import code_read
from src.skills.code.write import code_write
from src.skills.code.search import code_search


def test_file_read_relative_uses_active_cwd(tmp_path):
    base = tmp_path / "a"
    base.mkdir()
    (base / "x.txt").write_text("hello-cwd", encoding="utf-8")

    _active_cwd.set(base)
    try:
        r = file_read("x.txt")
    finally:
        _active_cwd.set(None)

    assert r["success"] is True
    assert r["content"] == "hello-cwd"


def test_file_read_absolute_path_unchanged(tmp_path):
    # 绝对路径不依赖 _active_cwd(向后兼容红线)。
    base = tmp_path / "a"
    base.mkdir()
    target = base / "x.txt"
    target.write_text("abs-content", encoding="utf-8")

    _active_cwd.set(Path("/nonexistent/active/cwd"))  # 故意错基准
    try:
        r = file_read(str(target))
    finally:
        _active_cwd.set(None)

    assert r["success"] is True
    assert r["content"] == "abs-content"


def test_file_write_relative_uses_active_cwd(tmp_path, monkeypatch):
    # 确保进程 cwd 不在 tmp_path(证明写到了 _active_cwd 而非进程 cwd)。
    other = tmp_path / "proc_cwd"
    other.mkdir()
    monkeypatch.chdir(other)

    base = tmp_path / "a"
    base.mkdir()
    _active_cwd.set(base)
    try:
        r = file_write("out.txt", "wrote")
    finally:
        _active_cwd.set(None)

    assert r["success"] is True
    assert (base / "out.txt").read_text() == "wrote"
    # 且未落到进程 cwd
    assert not (other / "out.txt").exists()


def test_code_read_relative_uses_active_cwd(tmp_path, monkeypatch):
    other = tmp_path / "proc_cwd"
    other.mkdir()
    monkeypatch.chdir(other)

    base = tmp_path / "a"
    base.mkdir()
    (base / "rel.py").write_text("print('hi')\n", encoding="utf-8")

    _active_cwd.set(base)
    try:
        r = code_read("rel.py")
    finally:
        _active_cwd.set(None)

    assert r["success"] is True
    assert "print" in r["content"]


def test_code_write_relative_uses_active_cwd(tmp_path, monkeypatch):
    other = tmp_path / "proc_cwd"
    other.mkdir()
    monkeypatch.chdir(other)

    base = tmp_path / "a"
    base.mkdir()
    _active_cwd.set(base)
    try:
        r = code_write("rel.py", "x = 1\n")
    finally:
        _active_cwd.set(None)

    assert r["success"] is True
    assert (base / "rel.py").read_text() == "x = 1\n"
    assert not (other / "rel.py").exists()


def test_code_search_relative_uses_active_cwd(tmp_path, monkeypatch):
    other = tmp_path / "proc_cwd"
    other.mkdir()
    monkeypatch.chdir(other)

    base = tmp_path / "a"
    base.mkdir()
    (base / "rel.py").write_text("UNIQUE_TOKEN_HERE = 1\n", encoding="utf-8")

    _active_cwd.set(base)
    try:
        r = code_search("UNIQUE_TOKEN_HERE", path=".", recursive=False)
    finally:
        _active_cwd.set(None)

    assert r["success"] is True
    assert r["total_matches"] >= 1
    # 命中文件应在 base 下
    assert any(str(base) in m["file"] for m in r["matches"])


def test_code_search_no_path_uses_active_cwd(tmp_path, monkeypatch):
    # 无 path 时 fallback _active_cwd(而非裸 Path.cwd())。
    other = tmp_path / "proc_cwd"
    other.mkdir()
    monkeypatch.chdir(other)

    base = tmp_path / "a"
    base.mkdir()
    (base / "np.py").write_text("NEEDLE_NP = 2\n", encoding="utf-8")

    _active_cwd.set(base)
    try:
        r = code_search("NEEDLE_NP")
    finally:
        _active_cwd.set(None)

    assert r["success"] is True
    assert r["total_matches"] >= 1
    assert any(str(base) in m["file"] for m in r["matches"])


def test_relative_falls_back_to_process_cwd_when_unset(tmp_path, monkeypatch):
    # 不 set _active_cwd:相对路径走 Path.cwd()(与改造前行为一致)。
    monkeypatch.chdir(tmp_path)
    _active_cwd.set(None)
    (tmp_path / "fallback.txt").write_text("fb", encoding="utf-8")

    r = file_read("fallback.txt")
    assert r["success"] is True
    assert r["content"] == "fb"


def test_safe_path_traversal_still_blocked(tmp_path):
    # 红线:.. 遍历防护不可被简化掉。
    from src.skills.primitive.file_tool import _safe_path

    try:
        _safe_path("../escape.txt")
    except ValueError:
        return
    raise AssertionError("Path traversal '..' should be rejected")


def test_safe_path_base_restriction_still_enforced(tmp_path):
    # 红线:base 限制逻辑(:43-49)不动。
    from src.skills.primitive.file_tool import _safe_path

    base = tmp_path / "base"
    base.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("x")
    try:
        _safe_path(str(outside), base=str(base))
    except ValueError:
        return
    raise AssertionError("Path outside base should be rejected")
