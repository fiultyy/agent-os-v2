"""Tests for P2 prompt_cache.apply_cache_control (Anthropic cache_control injection)."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

try:
    import pysqlite3

    sys.modules["sqlite3"] = pysqlite3
    sys.modules["sqlite3.dbapi2"] = pysqlite3.dbapi2
except ImportError:
    pass

from src.services.prompt_cache import apply_cache_control, build_marker


class TestBuildMarker:
    def test_default_5m(self) -> None:
        assert build_marker() == {"type": "ephemeral"}

    def test_1h(self) -> None:
        assert build_marker("1h") == {"type": "ephemeral", "ttl": "1h"}


class TestApplyCacheControl:
    def test_injects_at_static_boundary_str_content(self) -> None:
        msgs = [
            {"role": "system", "content": "base"},
            {"role": "system", "content": "tools"},
            {"role": "user", "content": "hi"},
        ]
        out = apply_cache_control(msgs, static_count=2)
        # static end (index 1, tools) → content wrapped with cache_control
        assert isinstance(out[1]["content"], list)
        assert out[1]["content"][-1]["cache_control"] == {"type": "ephemeral"}
        # base (index 0) untouched
        assert out[0]["content"] == "base"
        # user (index 2) untouched
        assert out[2] == {"role": "user", "content": "hi"}

    def test_does_not_mutate_input(self) -> None:
        msgs = [{"role": "system", "content": "base"}]
        apply_cache_control(msgs, static_count=1)
        assert msgs[0]["content"] == "base"  # original unchanged

    def test_invalid_static_count_noop(self) -> None:
        msgs = [{"role": "system", "content": "base"}]
        assert apply_cache_control(msgs, 0)[0]["content"] == "base"
        assert apply_cache_control(msgs, 99)[0]["content"] == "base"
        assert apply_cache_control([], 1) == []

    def test_ttl_1h_propagated(self) -> None:
        msgs = [{"role": "system", "content": "base"}]
        out = apply_cache_control(msgs, static_count=1, ttl="1h")
        assert out[0]["content"][-1]["cache_control"] == {
            "type": "ephemeral",
            "ttl": "1h",
        }

    def test_list_content_marker_on_last_block(self) -> None:
        msgs = [
            {"role": "system", "content": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]},
        ]
        out = apply_cache_control(msgs, static_count=1)
        assert out[0]["content"][-1]["cache_control"] == {"type": "ephemeral"}
        assert "cache_control" not in out[0]["content"][0]
