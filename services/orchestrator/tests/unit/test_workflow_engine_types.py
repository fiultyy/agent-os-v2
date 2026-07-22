"""W-P0-1 单测:workflow_engine type 定义段 + schema registry。

verify(design §5 W-P0-1):
- WorkflowNodesSpec.model_validate({'nodes':[{'prompt':'hi'}]}) 通过
- prompt:'' 拒(Field min_length=1)
- resolve_schema('text') 返 None(passthrough)
- resolve_schema(未知) 返 None 或抛 KeyError
"""

import pytest
from pydantic import ValidationError

from harness.workflow_engine import (
    NodeResult,
    WorkflowContext,
    WorkflowNodeSpec,
    WorkflowNodesSpec,
    WorkflowResult,
    register_schema,
    resolve_schema,
)


def test_nodes_spec_minimal_ok():
    spec = WorkflowNodesSpec.model_validate({"nodes": [{"prompt": "hi"}]})
    assert len(spec.nodes) == 1
    assert spec.nodes[0].prompt == "hi"
    assert spec.fan_in == "list"
    assert spec.timeout_per_node_ms == 120000


def test_node_spec_prompt_empty_rejected():
    with pytest.raises(ValidationError):
        WorkflowNodeSpec.model_validate({"prompt": ""})


def test_nodes_spec_empty_list_rejected():
    with pytest.raises(ValidationError):
        WorkflowNodesSpec.model_validate({"nodes": []})


def test_resolve_schema_text_passthrough():
    assert resolve_schema("text") is None
    assert resolve_schema(None) is None


def test_resolve_schema_unknown_returns_none():
    # design 接受 KeyError 或 None — 本实现选 None(ponytail:不阻断 fan-out)
    assert resolve_schema("does-not-exist") is None


def test_register_and_resolve_schema():
    from pydantic import BaseModel

    class Out(BaseModel):
        x: int = 0

    register_schema("test_out", Out)
    assert resolve_schema("test_out") is Out


def test_context_defaults():
    import asyncio

    from pydantic_ai.usage import RunUsage

    ctx = WorkflowContext(session_id="s", agent_id_prefix="a", run_id="r")
    assert ctx.concurrency == 8
    assert ctx.dry_counter == 0
    assert isinstance(ctx.total_usage, RunUsage)
    assert ctx.abort is None
    assert ctx.journal is None
    assert ctx.worktree_manager is None
    assert ctx.seen == set()
    # 编译期只引用 asyncio 防 unused 警告
    asyncio.Event()


def test_result_dataclasses():
    nr = NodeResult(label="n", agent_id="a")
    assert nr.status == "success"
    assert nr.output is None
    wr = WorkflowResult(
        status="success",
        node_results=[nr],
        total_usage=nr.usage,
        elapsed_ms=10,
        node_count=1,
        run_id="wf_x",
    )
    assert wr.node_count == 1
