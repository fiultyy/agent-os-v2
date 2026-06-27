"""L2: tool wiring — guardrail dict redaction, handler-signature alignment,
and the registry/catalog mirror that the清单制 register in engine.py relies on.

These run without importing engine.py (which needs httpx); they exercise the
primitives directly: the Guardrail, the ToolExecutor, the ToolRegistry catalog
mirror, and the file_read handler that chat.py now defaults to.
"""

import importlib.util
from pathlib import Path

import pytest

from src.tools.guardrail import Guardrail
from src.tools.executor import ToolExecutor
from src.tools.registry import ToolRegistry
from src.tools.catalog import ToolLayer


# ── Guardrail.check_output dict redaction (was: non-str → return True) ──


@pytest.mark.asyncio
async def test_check_output_redacts_sensitive_dict() -> None:
    g = Guardrail()
    allowed, reason = await g.check_output({"password": "secret123"})
    assert not allowed
    assert "Sensitive" in reason


@pytest.mark.asyncio
async def test_check_output_redacts_nested_list_dict() -> None:
    g = Guardrail()
    # db_query-style result: rows of credentials.
    allowed, _ = await g.check_output([{"id": 1, "api_key": "sk-abc"}])
    assert not allowed


@pytest.mark.asyncio
async def test_check_output_passes_clean_dict() -> None:
    g = Guardrail()
    allowed, _ = await g.check_output({"ok": True, "count": 3, "items": ["a", "b"]})
    assert allowed


@pytest.mark.asyncio
async def test_check_output_still_redacts_str() -> None:
    """Regression: the str path still works after the dict branch was added."""
    g = Guardrail()
    allowed, _ = await g.check_output("config token=abc123 here")
    assert not allowed


# ── chat.py default tool: file_read(path=...) signature alignment ──────


def _load_file_tool():
    """Load file_tool.py directly from its file path, bypassing the
    src.skills package __init__ (which would pull pyyaml/httpx)."""
    path = Path(__file__).resolve().parent.parent / "src" / "skills" / "primitive" / "file_tool.py"
    spec = importlib.util.spec_from_file_location("_l2_file_tool", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_default_tool_file_read_degrades_gracefully() -> None:
    """file_read (the new chat.py default) accepts path=... and never raises."""
    file_tool = _load_file_tool()
    result = file_tool.file_read(path="/nonexistent/l2-probe")
    assert result["success"] is False        # File not found, NOT a crash
    assert result["path"] == "/nonexistent/l2-probe"


@pytest.mark.asyncio
async def test_executor_runs_registered_file_read_with_path_arg() -> None:
    """End-to-end: register file_read, execute with {path: ...} (the chat.py
    default args) → status success, output is the handler's dict. This proves
    the default tool_name/tool_args are valid (no 'not found', no signature
    mismatch)."""
    file_tool = _load_file_tool()
    registry = ToolRegistry()
    registry.register(
        "file_read", file_tool.file_read,
        description="read", parameters={"path": {}}, layer=ToolLayer.PRIMITIVE,
    )
    executor = ToolExecutor(registry)

    result = await executor.execute("file_read", {"path": "/nonexistent/l2-probe"})
    assert result["status"] == "success"            # tool ran (file-missing is internal)
    assert result["output"]["success"] is False
    assert result["error"] is None


# ── ToolRegistry catalog mirror (the mechanism the清单制 register depends on) ──


def test_registry_register_mirrors_into_catalog() -> None:
    """engine.py relies on register() also populating the L3.4 catalog so the
    logged list_tools/catalog counts agree."""
    registry = ToolRegistry()

    async def _noop(**_):
        return {}

    registry.register("t1", _noop, layer=ToolLayer.PRIMITIVE)
    registry.register("t2", _noop, layer=ToolLayer.SKILL)

    assert len(registry.list_tools()) == 2
    assert registry.get_catalog().count() == 2
    layers = {e.layer for e in registry.get_catalog().list_all()}
    assert layers == {ToolLayer.PRIMITIVE, ToolLayer.SKILL}
