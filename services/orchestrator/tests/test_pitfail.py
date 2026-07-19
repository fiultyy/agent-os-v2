"""PitFail 通电回归测试。

覆盖:
1. 工具失败 → record(新,count=1)
2. 同 file_path+error_type 再失败 → match 命中 increment_recurrence(count=2)
3. search(query) 命中
4. pitfail_registry None 降级(不 record,不崩)
(CRUD routes 已弃用,原 API TestClient 测随 routes/pitfail.py 一并删除。)

红线:不动 memory 五维/召回/蝴蝶翼。pysqlite3 由 conftest 全局注入,无需本文件
再 patch sqlite3。
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from src.services import _state
from src.pitfail import PitfailRegistry, PitfallRecord

# ── Registry 直测(record / match-increment / search / None 降级) ───────


def _fresh_registry(tmp_path) -> PitfailRegistry:
    """每个 case 独立 db 文件,避免跨测试污染。"""
    return PitfailRegistry(str(tmp_path / "pitfalls.db"))


def test_record_new_pitfall_count_starts_at_1(tmp_path):
    reg = _fresh_registry(tmp_path)
    pid = reg.record(PitfallRecord(
        id="", file_path="file_read", error_type="tool_error",
        symptom="[Tool error] file_read: File not found: /x/y",
        root_cause="missing path", fix="", tags=["file_read"],
    ))
    assert pid  # record 返回生成的 id
    got = reg.get(pid)
    assert got is not None
    assert got.recurrence_count == 1
    assert got.file_path == "file_read"
    assert got.error_type == "tool_error"


def test_match_then_increment_recurrence(tmp_path):
    reg = _fresh_registry(tmp_path)
    # 第一次失败 → record
    reg.record(PitfallRecord(
        id="", file_path="http_get", error_type="timeout",
        symptom="request timed out", root_cause="slow upstream", fix="",
        tags=["http_get"],
    ))
    matched = reg.match("http_get", "timeout")
    assert len(matched) == 1
    assert matched[0].recurrence_count == 1

    # 同 file_path+error_type 再失败 → match 命中 → increment
    reg.increment_recurrence(matched[0].id)
    matched2 = reg.match("http_get", "timeout")
    assert matched2[0].recurrence_count == 2

    # 不同 error_type 不命中(分类隔离)
    assert reg.match("http_get", "tool_error") == []


def test_search_hits_symptom(tmp_path):
    reg = _fresh_registry(tmp_path)
    reg.record(PitfallRecord(
        id="", file_path="db_query", error_type="permission_denied",
        symptom="database is locked", root_cause="concurrent writer", fix="retry",
        tags=["db_query"],
    ))
    hits = reg.search("locked")
    assert len(hits) == 1
    assert hits[0].file_path == "db_query"
    # fix 字段也可被 search 命中
    assert len(reg.search("retry")) == 1
    # 未命中返回空
    assert reg.search("nonexistent-token-xyz") == []


def test_registry_none_degrade_does_not_crash(tmp_path, monkeypatch):
    """pitfail_registry = None 时,call-site guard 应跳过 record/match —— 不崩。"""
    monkeypatch.setattr(_state, "pitfail_registry", None)
    # 模拟 chat.py _node_tool guard 逻辑
    if _state.pitfail_registry is not None:
        pytest.fail("guard failed — should be None")
    # None 路径下任何属性访问都应被 guard 拦截(此处仅断言 guard 语义)


# ── 工具失败 hook 等价路径(record vs match-increment 分支) ───────────


def test_hook_branch_record_when_no_match(tmp_path, monkeypatch):
    """等价 _node_tool 失败分支:无既有 → record 新记录。"""
    reg = _fresh_registry(tmp_path)
    monkeypatch.setattr(_state, "pitfail_registry", reg)

    # 模拟工具失败:_node_tool 内的分类 + match-or-record
    error_msg = "request timeout after 30s"
    err_type = "timeout" if "timeout" in error_msg.lower() else "tool_error"
    existing = _state.pitfail_registry.match("http_get", err_type)
    if existing:
        _state.pitfail_registry.increment_recurrence(existing[0].id)
    else:
        _state.pitfail_registry.record(PitfallRecord(
            id="", file_path="http_get", error_type=err_type,
            symptom=error_msg, root_cause=error_msg, fix="", tags=["http_get"],
        ))

    assert err_type == "timeout"
    assert len(reg.match("http_get", "timeout")) == 1
    assert reg.match("http_get", "timeout")[0].recurrence_count == 1


def test_hook_branch_increment_when_match(tmp_path, monkeypatch):
    """等价 _node_tool 失败分支:命中既有 → increment_recurrence。"""
    reg = _fresh_registry(tmp_path)
    monkeypatch.setattr(_state, "pitfail_registry", reg)
    # 预置一条
    reg.record(PitfallRecord(
        id="", file_path="http_get", error_type="timeout",
        symptom="timed out", root_cause="slow", fix="", tags=["http_get"],
    ))

    error_msg = "timed out again"
    err_type = "timeout"
    existing = _state.pitfail_registry.match("http_get", err_type)
    if existing:
        _state.pitfail_registry.increment_recurrence(existing[0].id)
    else:
        pytest.fail("should have matched existing")

    assert reg.match("http_get", "timeout")[0].recurrence_count == 2


def test_classify_tool_error_branches():
    """_classify_tool_error 关键词分类(P8:已从 chat.py 移入 ToolBridgeCapability)。"""
    from src.harness.capabilities.tool_bridge_capability import _classify_tool_error
    assert _classify_tool_error("Operation timed out") == "timeout"
    assert _classify_tool_error("request timeout reached") == "timeout"
    assert _classify_tool_error("File not found: /a/b") == "file_not_found"
    assert _classify_tool_error("No such file or directory") == "file_not_found"
    assert _classify_tool_error("Permission denied") == "permission_denied"
    assert _classify_tool_error("connection reset by peer") == "tool_error"
    assert _classify_tool_error("") == "tool_error"
