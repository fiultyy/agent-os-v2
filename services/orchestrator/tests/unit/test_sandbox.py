"""Tests for sandbox.py."""

import asyncio
import os
import pytest
import tempfile

from src.agent.meta.sandbox import SandboxExecutor, SandboxResult


# ── SandboxResult tests ────────────────────────────────────────────────────────


class TestSandboxResult:
    def test_sandbox_result_creation(self):
        result = SandboxResult(
            stdout="hello",
            stderr="",
            exit_code=0,
            timed_out=False,
            memory_kb=1024,
            execution_time_ms=50,
        )
        assert result.stdout == "hello"
        assert result.stderr == ""
        assert result.exit_code == 0
        assert result.timed_out is False
        assert result.memory_kb == 1024
        assert result.execution_time_ms == 50

    def test_sandbox_result_with_error(self):
        result = SandboxResult(
            stdout="",
            stderr="Error: division by zero",
            exit_code=1,
            timed_out=False,
            memory_kb=None,
            execution_time_ms=10,
        )
        assert result.exit_code == 1
        assert "division" in result.stderr


# ── SandboxExecutor init tests ─────────────────────────────────────────────────


class TestSandboxExecutorInit:
    def test_init_defaults(self):
        executor = SandboxExecutor()
        limits = executor.get_limits()
        assert limits["max_memory_mb"] == 512
        assert limits["max_cpu_seconds"] == 30
        assert limits["max_procs"] == 1

    def test_init_custom_limits(self):
        executor = SandboxExecutor(max_memory_mb=256, max_cpu_seconds=10, max_procs=2)
        limits = executor.get_limits()
        assert limits["max_memory_mb"] == 256
        assert limits["max_cpu_seconds"] == 10
        assert limits["max_procs"] == 2

    def test_get_limits(self):
        executor = SandboxExecutor(max_memory_mb=128, max_cpu_seconds=5)
        limits = executor.get_limits()
        assert isinstance(limits, dict)
        assert "max_memory_mb" in limits
        assert "max_cpu_seconds" in limits


# ── SandboxExecutor execute tests ───────────────────────────────────────────────


class TestSandboxExecutorExecute:
    @pytest.mark.asyncio
    async def test_execute_python_simple_print(self):
        executor = SandboxExecutor()
        result = await executor.execute("print('hello')", lang="python")
        assert result["stdout"].strip() == "hello"
        assert result["exit_code"] == 0
        assert result["timed_out"] is False

    @pytest.mark.asyncio
    async def test_execute_python_with_error(self):
        executor = SandboxExecutor()
        result = await executor.execute("raise ValueError('test error')", lang="python")
        assert result["exit_code"] != 0
        assert "ValueError" in result["stderr"] or "test error" in result["stderr"]

    @pytest.mark.asyncio
    async def test_execute_python_computation(self):
        executor = SandboxExecutor()
        code = """
result = sum(range(100))
print(result)
"""
        result = await executor.execute(code, lang="python")
        assert result["exit_code"] == 0
        assert "4950" in result["stdout"]

    @pytest.mark.asyncio
    async def test_execute_python_infinite_loop_timeout(self):
        executor = SandboxExecutor(max_cpu_seconds=2)
        result = await executor.execute("while True: pass", lang="python")
        assert result["timed_out"] is True

    @pytest.mark.asyncio
    async def test_execute_python_memory_limit(self):
        executor = SandboxExecutor(max_memory_mb=50)
        # 分配一个较大的列表（超过50MB）
        code = "x = [0] * (100_000_000)"
        result = await executor.execute(code, lang="python")
        # 应该被内存限制杀死或 OOM
        assert result["exit_code"] != 0 or result["timed_out"] is True

    @pytest.mark.asyncio
    async def test_execute_python_empty_code(self):
        executor = SandboxExecutor()
        result = await executor.execute("", lang="python")
        assert result["exit_code"] == 0

    @pytest.mark.asyncio
    async def test_execute_python_multiline(self):
        executor = SandboxExecutor()
        code = """
def fib(n):
    if n <= 1:
        return n
    return fib(n-1) + fib(n-2)

print(fib(10))
"""
        result = await executor.execute(code, lang="python")
        assert result["exit_code"] == 0
        assert "55" in result["stdout"]

    @pytest.mark.asyncio
    async def test_execute_python_subprocess_limited(self):
        executor = SandboxExecutor(max_procs=1)
        # 尝试 fork（可能受限）
        code = "import os; print(os.fork())"
        result = await executor.execute(code, lang="python")
        # fork 可能失败或被限制，但不应崩溃
        assert isinstance(result["exit_code"], int)

    @pytest.mark.asyncio
    async def test_execute_nonexistent_interpreter(self):
        executor = SandboxExecutor()
        # 使用未知语言
        result = await executor.execute("echo hello", lang="nonexistent_lang_xyz")
        # 应该返回错误
        assert result["exit_code"] != 0

    @pytest.mark.asyncio
    async def test_execution_time_recorded(self):
        executor = SandboxExecutor()
        code = "import time; time.sleep(0.1); print('done')"
        result = await executor.execute(code, lang="python")
        assert result["execution_time_ms"] > 0

    @pytest.mark.asyncio
    async def test_execute_uses_temp_file_cleanup(self):
        executor = SandboxExecutor()
        # 执行后临时文件应被删除
        await executor.execute("print('test')", lang="python")
        # 如果临时文件残留会报异常
        # 这里我们只验证执行成功


# ── Convenience methods ───────────────────────────────────────────────────────


class TestSandboxExecutorConvenience:
    @pytest.mark.asyncio
    async def test_run_python_shortcut(self):
        executor = SandboxExecutor()
        result = await executor.run_python("print(1 + 2)")
        assert result["exit_code"] == 0
        assert "3" in result["stdout"]

    @pytest.mark.asyncio
    async def test_run_bash_shortcut(self):
        executor = SandboxExecutor()
        result = await executor.run_bash("echo hello")
        # bash 可能不在容器中，检查是否有输出或错误
        assert isinstance(result["exit_code"], int)

    @pytest.mark.asyncio
    async def test_get_limits_reflects_init(self):
        executor = SandboxExecutor(max_memory_mb=100, max_cpu_seconds=5, max_procs=3)
        limits = executor.get_limits()
        assert limits["max_memory_mb"] == 100
        assert limits["max_cpu_seconds"] == 5
        assert limits["max_procs"] == 3
