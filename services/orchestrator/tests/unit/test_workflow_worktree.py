"""W-P2-3 单测:workflow_engine.worktree(WorktreeManager + _chdir)。

verify(design §5 W-P2-3):
- (a) acquire/release 调 git worktree add/remove(mock subprocess.run;
  acquire 不存在 → 调 git worktree add --detach;release → git worktree remove --force)。
- (b) __aexit__ 释放所有未 release 的 worktree(acquire 后不 release,__aexit__ 清空 _refs)。
- (c) chdir 上下文还原:_chdir 切到目标 path,yield 后还原 prev cwd(不变量)。
- (d) 并发 2 worktree node 串行执行:两 task 同时入 ``_sem()``,_wt_semaphore(1) 保证
  acquire/agent.run/release 块不重叠(用 record timestamp 验证 non-overlap)。
- opt-in 默认 None 走 session cwd:node.isolation=None 时 _spawn_agent_inner 不调 worktree
  (经 run 路径验证 isolation=None 时 fake agent.run 不切 cwd)。
"""

import asyncio
import os
from pathlib import Path
from unittest.mock import patch

import harness.workflow_engine as wf_mod
from harness.workflow_engine import (
    WorktreeManager,
    WorkflowContext,
    WorkflowEngine,
    WorkflowNodeSpec,
    WorkflowNodesSpec,
    WorktreeManager as _WM,  # alias 防被 import 覆盖
    _chdir,
)
from harness.workflow_engine.worktree import _chdir as _chdir_mod
from pydantic_ai.usage import RunUsage

# ── sync wrapper(避开 pytest-asyncio loop pollution)──
_LOOP = None


def run_async(coro):
    global _LOOP
    if _LOOP is None or _LOOP.is_closed():
        _LOOP = asyncio.new_event_loop()
        asyncio.set_event_loop(_LOOP)
    return _LOOP.run_until_complete(coro)


# ─────────────────────────────────────────────────────────────────────
# (a) acquire/release 调 git worktree add/remove(mock subprocess)
# ─────────────────────────────────────────────────────────────────────
def test_acquire_calls_git_worktree_add_when_missing(tmp_path):
    calls = []

    def fake_run(cmd, *a, **kw):
        calls.append(list(cmd))
        # 模拟 git worktree add 创建目录(acquire 的 wt.exists() 检查后续调用跳过 add)
        if "add" in cmd:
            # cmd = ['git', 'worktree', 'add', '--detach', str(wt), base_ref]
            wt_path = Path(cmd[4])
            wt_path.mkdir(parents=True, exist_ok=True)
        return 0  # subprocess.run 不返回 CompletedProcess 时用 0 占位

    wt = WorktreeManager(base=tmp_path)
    with patch("harness.workflow_engine.worktree.subprocess.run", side_effect=fake_run):
        run_async(wt.acquire("agent_a", "run_1", base_ref="HEAD"))

    # 第一次 acquire:wt 不存在 → 调 git worktree add --detach <path> HEAD
    add_calls = [c for c in calls if "add" in c]
    assert len(add_calls) == 1, f"expected 1 git worktree add, got {add_calls}"
    assert add_calls[0][:3] == ["git", "worktree", "add"]
    assert "--detach" in add_calls[0]
    # 统一路径:<base>/.claude/worktrees/<run_id>/<agent_id>
    assert add_calls[0][4] == str(tmp_path / ".claude" / "worktrees" / "run_1" / "agent_a")
    assert add_calls[0][5] == "HEAD"


def test_acquire_idempotent_when_wt_exists(tmp_path):
    """wt 已存在(acquire 第二次调)→ 不重复 git worktree add。"""
    calls = []

    def fake_run(cmd, *a, **kw):
        calls.append(list(cmd))
        if "add" in cmd:
            Path(cmd[4]).mkdir(parents=True, exist_ok=True)
        return 0

    wt = WorktreeManager(base=tmp_path)
    with patch("harness.workflow_engine.worktree.subprocess.run", side_effect=fake_run):
        run_async(wt.acquire("agent_a", "run_1"))
        run_async(wt.acquire("agent_a", "run_1"))  # 第二次:wt 已存在 → 跳过 add
    add_calls = [c for c in calls if "add" in c]
    assert len(add_calls) == 1, "idempotent acquire 不应重复 git worktree add"


def test_release_calls_git_worktree_remove(tmp_path):
    calls = []

    def fake_run(cmd, *a, **kw):
        calls.append(list(cmd))
        if "add" in cmd:
            Path(cmd[4]).mkdir(parents=True, exist_ok=True)
        return 0

    wt = WorktreeManager(base=tmp_path)
    with patch("harness.workflow_engine.worktree.subprocess.run", side_effect=fake_run):
        run_async(wt.acquire("agent_a", "run_1"))
        run_async(wt.release("agent_a"))

    rm_calls = [c for c in calls if "remove" in c]
    assert len(rm_calls) == 1, f"expected 1 git worktree remove, got {rm_calls}"
    # cmd = ['git', 'worktree', 'remove', '--force', '<path>']
    assert rm_calls[0][:4] == ["git", "worktree", "remove", "--force"]
    assert rm_calls[0][4] == str(tmp_path / ".claude" / "worktrees" / "run_1" / "agent_a")
    # release 后 _refs 移除(__aexit__ 不重复释放)
    assert "agent_a" not in wt._refs


def test_release_keep_true_skips_remove(tmp_path):
    """keep=True → 不调 git worktree remove(保留供 inspect)。"""
    calls = []

    def fake_run(cmd, *a, **kw):
        calls.append(list(cmd))
        if "add" in cmd:
            Path(cmd[4]).mkdir(parents=True, exist_ok=True)
        return 0

    wt = WorktreeManager(base=tmp_path)
    with patch("harness.workflow_engine.worktree.subprocess.run", side_effect=fake_run):
        run_async(wt.acquire("agent_a", "run_1"))
        run_async(wt.release("agent_a", keep=True))
    rm_calls = [c for c in calls if "remove" in c]
    assert rm_calls == [], "keep=True 不应调 git worktree remove"


def test_release_unknown_agent_noop():
    """release 未 acquire 的 agent_id → noop 不 raise(check=False 兜底)。"""
    wt = WorktreeManager(base=Path("/tmp/fake-repo-unk"))
    run_async(wt.release("nonexistent"))  # 不 raise


# ─────────────────────────────────────────────────────────────────────
# (b) __aexit__ 释放所有未 release 的 worktree
# ─────────────────────────────────────────────────────────────────────
def test_aexit_releases_all_unreleased_worktrees(tmp_path):
    calls = []

    def fake_run(cmd, *a, **kw):
        calls.append(list(cmd))
        if "add" in cmd:
            Path(cmd[4]).mkdir(parents=True, exist_ok=True)
        return 0

    async def scenario():
        wt = WorktreeManager(base=tmp_path)
        with patch("harness.workflow_engine.worktree.subprocess.run", side_effect=fake_run):
            async with wt:
                await wt.acquire("agent_a", "run_1")
                await wt.acquire("agent_b", "run_1")
                await wt.release("agent_a")  # 显式释放
                # agent_b 不 release → __aexit__ 兜底
                assert "agent_b" in wt._refs
            # async with 退出后:_refs 应被 __aexit__ 清空
            assert wt._refs == {}, f"__aexit__ 应清空 _refs, got {wt._refs}"

    run_async(scenario())

    add_calls = [c for c in calls if "add" in c]
    rm_calls = [c for c in calls if "remove" in c]
    assert len(add_calls) == 2, "2 个 acquire → 2 个 add"
    # 1 个显式 release(agent_a)+ 1 个 __aexit__ 兜底(agent_b)= 2 个 remove
    assert len(rm_calls) == 2, f"expected 2 remove (explicit + aexit), got {rm_calls}"


# ─────────────────────────────────────────────────────────────────────
# (c) chdir 上下文还原:_chdir 切到目标,yield 后还原 prev cwd
# ─────────────────────────────────────────────────────────────────────
def test_chdir_restores_previous_cwd(tmp_path):
    prev = Path.cwd()
    target = tmp_path / "wt_target"
    target.mkdir()
    with _chdir(target):
        assert Path.cwd().resolve() == target.resolve(), "chdir 内应切到 target"
    assert Path.cwd() == prev, "chdir 退出后应还原 prev cwd"


def test_chdir_restores_on_exception(tmp_path):
    """yield 块抛异常 → finally 仍还原 cwd(不变量)。"""
    prev = Path.cwd()
    target = tmp_path / "wt_target_exc"
    target.mkdir()
    try:
        with _chdir(target):
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert Path.cwd() == prev, "异常路径下 finally 仍应还原 prev cwd"


# ─────────────────────────────────────────────────────────────────────
# (d) 并发 2 worktree node 串行执行:_wt_semaphore(1) 保证 non-overlap
# ─────────────────────────────────────────────────────────────────────
def test_wt_semaphore_serializes_concurrent_worktree_nodes():
    """两 task 同时进 _sem() → acquire/agent.run/release 块 non-overlap。

    记录每 task 的 active 时间窗;两 task 不重叠(任一时刻 ≤ 1 task active)。
    """

    async def scenario():
        wt = WorktreeManager(base=Path("/tmp/fake-repo-sem"))
        timeline = []  # list of ("start"|"end", task_id)

        async def task(task_id):
            def fake_run(cmd, *a, **kw):
                if "add" in cmd:
                    Path(cmd[4]).mkdir(parents=True, exist_ok=True)
                return 0
            with patch("harness.workflow_engine.worktree.subprocess.run", side_effect=fake_run):
                async with wt._sem():
                    timeline.append(("start", task_id))
                    await asyncio.sleep(0.05)  # 模拟 agent.run 等待
                    timeline.append(("end", task_id))

        await asyncio.gather(task("A"), task("B"))

        # 提取时间窗:A[start..end] 与 B[start..end] 不重叠
        events = timeline
        # 验证:在任一 "start" 与其对应 "end" 之间,不应出现另一 task 的 "start"
        # 简化断言:events 序列应为 [start_A, end_A, start_B, end_B] 或反之(纯串行)
        # 取每 task 的 start/end 索引
        starts = [(i, t) for i, (k, t) in enumerate(events) if k == "start"]
        ends = [(i, t) for i, (k, t) in enumerate(events) if k == "end"]
        for (s_i, s_t), (e_i, e_t) in zip(starts, ends):
            assert s_t == e_t, "start/end 配对应同 task"
            # 任一其他 task 的 start 不应在 [s_i, e_i] 之间
            for j, (_, other_t) in enumerate(events):
                if other_t == s_t:
                    continue
                if events[j][0] == "start" and s_i < j < e_i:
                    return False, f"task {other_t} start 在 {s_t} active 窗内 → 重叠"
        return True, "串行 non-overlap"

    ok, msg = run_async(scenario())
    assert ok, f"_wt_semaphore(1) 未串行化:{msg}"


def test_wt_semaphore_is_one():
    """_wt_semaphore 默认 None(懒建);首次 _sem() 后建 Semaphore(1)。

    concurrency=1 是 worktree node 串行化的根(os.chdir 进程全局)。
    """
    wt = WorktreeManager(base=Path("/tmp/fake-repo-sem-val"))
    # 懒建:模块导入时不应建 Semaphore(无 running loop 会 raise)
    assert wt._wt_semaphore is None
    run_async(_no_op_async(wt))
    sem = wt._sem()
    # 验证 Semaphore 的内部 _value 经 _bounded_slots 推断:用 try_acquire 验证 cap=1
    # ponytail:不直接戳 _value(私有 API),用 acquire 阻塞行为验证
    import asyncio as _aio
    loop = _aio.new_event_loop()

    async def check():
        async with wt._sem():
            # 持锁期间另一 acquire 应阻塞(timeout 证明 cap=1)
            try:
                await asyncio.wait_for(wt._sem().__aenter__(), timeout=0.05)
                return False  # 不应能立即获取(cap>1)
            except asyncio.TimeoutError:
                return True  # 阻塞 → cap=1
    try:
        result = loop.run_until_complete(check())
    finally:
        loop.close()
    assert result, "_wt_semaphore 应 cap=1"


async def _no_op_async(wt):
    """触发 _sem() 懒建。"""
    wt._sem()


# ─────────────────────────────────────────────────────────────────────
# opt-in 默认 None 走 session cwd:isolation=None 时 _spawn_agent 不切 cwd
# ─────────────────────────────────────────────────────────────────────
class _FakeRunResult:
    def __init__(self, output):
        self.output = output


class _FakeAgent:
    """记录 run 时 cwd,验证 isolation=None 不切 cwd / isolation='worktree' 切到 wt。"""

    def __init__(self, output="ok"):
        self._output = output
        self.run_cwd = None

    async def run(self, task_input, *, usage=None, usage_limits=None):
        self.run_cwd = Path.cwd()
        if usage is not None:
            usage.requests = (usage.requests or 0) + 1
            usage.input_tokens = (usage.input_tokens or 0) + 10
        return _FakeRunResult(self._output)


def _patch_build(agents):
    it = iter(agents)
    orig = wf_mod.build_native_agent

    def _fake(*a, **kw):
        return next(it)

    wf_mod.build_native_agent = _fake

    def restore():
        wf_mod.build_native_agent = orig

    return restore


def test_optin_default_none_does_not_set_worktree_manager():
    """isolation=None → _spawn_agent_inner 走 else 分支(不调 worktree)。

    WorkflowContext.worktree_manager 默认 None → _spawn_agent_inner 的
    ``use_worktree = node.isolation=='worktree' and wt_manager is not None``
    短路 False,agent.run 在 session cwd 执行。
    """
    fake_agent = _FakeAgent()
    restore = _patch_build([fake_agent])
    try:
        engine = WorkflowEngine()
        ctx = WorkflowContext(
            session_id="s1", agent_id_prefix="wf", run_id="wf_x",
        )
        assert ctx.worktree_manager is None, "默认 ctx.worktree_manager 应 None(opt-in)"
        node = WorkflowNodeSpec.model_validate({"prompt": "hi", "isolation": None})
        run_async(engine._spawn_agent(node, ctx))
        # isolation=None → agent.run 在 session cwd 执行,不切 worktree
        assert fake_agent.run_cwd == Path.cwd(), \
            f"isolation=None 不应切 cwd, got {fake_agent.run_cwd}"
    finally:
        restore()


def test_optin_worktree_acquires_and_changes_cwd(tmp_path):
    """isolation='worktree' + ctx.worktree_manager → _spawn_agent_inner 切 cwd 到 wt。

    mock subprocess.run(acquire 建 tmp dir);fake agent 记录 run 时 cwd;断言
    run_cwd == wt_path(切到 worktree)+ 完成后 session cwd 还原。
    """
    fake_agent = _FakeAgent()
    restore = _patch_build([fake_agent])
    session_cwd = Path.cwd()
    try:
        wt = WorktreeManager(base=tmp_path)
        engine = WorkflowEngine()
        ctx = WorkflowContext(
            session_id="s1", agent_id_prefix="wf", run_id="wf_wt",
            worktree_manager=wt,
        )

        def fake_run(cmd, *a, **kw):
            if "add" in cmd:
                Path(cmd[4]).mkdir(parents=True, exist_ok=True)
            return 0

        with patch("harness.workflow_engine.worktree.subprocess.run", side_effect=fake_run):
            node = WorkflowNodeSpec.model_validate({"prompt": "hi", "isolation": "worktree"})
            nr = run_async(engine._spawn_agent(node, ctx))

        # agent.run 切到了 worktree path
        expected_wt = tmp_path / ".claude" / "worktrees" / "wf_wt" / node.label
        assert fake_agent.run_cwd == expected_wt.resolve(), \
            f"isolation='worktree' 应切 cwd 到 {expected_wt}, got {fake_agent.run_cwd}"
        # _spawn_agent 完成后 session cwd 还原(_chdir finally restore)
        assert Path.cwd() == session_cwd, "_chdir 退出后应还原 session cwd"
        # release 已调(agent.run 后 _refs 清空)
        assert node.label not in wt._refs, "agent.run 后应 release worktree"
        # NodeResult status=success
        assert nr.status == "success"
    finally:
        restore()


def test_optin_worktree_isolation_but_no_manager_falls_back_to_session_cwd():
    """isolation='worktree' 但 ctx.worktree_manager=None → opt-in 短路走 session cwd。

    (防御:WT 配置不当时不崩,降级 session cwd 路径 — ponytail YAGNI 兜底。)
    """
    fake_agent = _FakeAgent()
    restore = _patch_build([fake_agent])
    try:
        engine = WorkflowEngine()
        ctx = WorkflowContext(
            session_id="s1", agent_id_prefix="wf", run_id="wf_nomanager",
        )
        assert ctx.worktree_manager is None
        node = WorkflowNodeSpec.model_validate({"prompt": "hi", "isolation": "worktree"})
        session_cwd = Path.cwd()
        run_async(engine._spawn_agent(node, ctx))
        # 降级:agent.run 在 session cwd(无 manager 时不切)
        assert fake_agent.run_cwd == session_cwd, \
            f"无 manager 时 isolation='worktree' 应回退 session cwd, got {fake_agent.run_cwd}"
    finally:
        restore()
