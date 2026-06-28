"""SandboxExecutor — 隔离进程执行，资源限制。

使用 subprocess + resource limits 实现隔离。
支持内存限制（RSS）和 CPU 时间限制。

注意：此沙箱是进程级隔离，不是容器级隔离。
适用于受信任代码的资源和超时控制，不适用于不可信代码的严格安全隔离。
"""

# NOT-WIRED (deferred: 隔离沙箱执行未接生产,仅 1 个单测文件引用;meta 三件套
# defer 孤岛之一,见 docs/mvp-iteration-roadmap.md:32 +
# docs/implementation-roadmap.md:136)。保留代码 + 单测(质量高,删再造成本高)。

from __future__ import annotations

import asyncio
import os
import resource
import signal
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from typing import Any


@dataclass
class SandboxResult:
    """沙箱执行结果。"""

    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool
    memory_kb: int | None  # Peak RSS in KB, None if not available
    execution_time_ms: int


class SandboxExecutor:
    """隔离进程执行器。

    使用 Python subprocess + resource.setrlimit 实现进程级资源限制。
    支持内存限制（RSS）和 CPU 时间限制。

    注意：
    - 内存限制通过 RLIMIT_AS（地址空间限制）实现，不是严格的 RSS 限制
    - CPU 时间限制通过 RLIMIT_CPU 实现
    - 对于不可信代码，应使用容器级隔离（如 Docker + seccomp）

    Attributes:
        max_memory_mb: 最大内存限制（MB），默认 512MB。
        max_cpu_seconds: 最大 CPU 时间限制（秒），默认 30s。
        max_procs: 最大子进程数，默认 1。
    """

    def __init__(
        self,
        max_memory_mb: int = 512,
        max_cpu_seconds: int = 30,
        max_procs: int = 1,
    ) -> None:
        """初始化 SandboxExecutor。

        Args:
            max_memory_mb: 最大内存限制（MB）。
            max_cpu_seconds: 最大 CPU 时间（秒）。
            max_procs: 最大子进程数。
        """
        self._max_memory = max_memory_mb * 1024 * 1024  # bytes
        self._max_cpu_seconds = max_cpu_seconds
        self._max_procs = max_procs

    def _get_limits(self) -> list[tuple[int, int]]:
        """返回 resource.setrlimit 参数列表。"""
        return [
            (resource.RLIMIT_AS, (self._max_memory, self._max_memory)),
            (resource.RLIMIT_CPU, (self._max_cpu_seconds, self._max_cpu_seconds)),
            (resource.RLIMIT_NPROC, (self._max_procs, self._max_procs)),
        ]

    def _create_execution_script(self, code: str, lang: str) -> tuple[str, str]:
        """生成临时代码脚本。

        Returns:
            (script_path, script_content)。
        """
        if lang == "python":
            # Python 直接执行
            script = code
            ext = ".py"
        elif lang == "javascript":
            # Node.js
            script = code
            ext = ".js"
        elif lang == "bash":
            script = code
            ext = ".sh"
        else:
            script = code
            ext = f".{lang}"

        return ext, script

    async def execute(self, code: str, lang: str = "python") -> dict[str, Any]:
        """在沙箱中执行代码，返回 stdout/stderr/exit_code。

        Args:
            code: 要执行的代码。
            lang: 语言类型，支持 python/javascript/bash。

        Returns:
            包含执行结果的字典：
            - stdout: 标准输出
            - stderr: 标准错误
            - exit_code: 退出码
            - timed_out: 是否超时
            - memory_kb: 峰值内存（KB）
            - execution_time_ms: 执行时间（毫秒）
        """
        ext, script_content = self._create_execution_script(code, lang)

        # 创建临时文件
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=ext,
            delete=False,
            encoding="utf-8",
        ) as f:
            script_path = f.name
            f.write(script_content)

        try:
            result = await self._run_process(script_path, lang)
            return {
                "stdout": result.stdout,
                "stderr": result.stderr,
                "exit_code": result.exit_code,
                "timed_out": result.timed_out,
                "memory_kb": result.memory_kb,
                "execution_time_ms": result.execution_time_ms,
            }
        finally:
            # 清理临时文件
            try:
                import os
                os.unlink(script_path)
            except OSError:
                pass

    async def _run_process(self, script_path: str, lang: str) -> SandboxResult:
        """运行子进程并应用资源限制。"""
        import time

        # 确定解释器
        if lang == "python":
            cmd = [sys.executable, script_path]
        elif lang == "javascript":
            cmd = ["node", script_path]
        elif lang == "bash":
            cmd = ["/bin/bash", script_path]
        else:
            cmd = [script_path]

        start_time = time.perf_counter()

        # 设置环境变量
        env = {
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "HOME": tempfile.gettempdir(),
        }

        # Record baseline memory before spawning child (RUSAGE_CHILDREN is cumulative)
        baseline_rss = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss

        proc: subprocess.Popen | None = None
        timed_out = False
        memory_kb: int | None = None
        stdout_data = b""
        stderr_data = b""
        exit_code = 0

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                env=env,
                preexec_fn=self._set_limits,  # type: ignore
            )

            # 使用轮询方式检测超时，这样可以及时杀死超时的进程
            timeout_seconds = self._max_cpu_seconds
            poll_interval = 0.1  # 100ms
            waited = 0.0
            timed_out = False
            killed_by_us = False

            while proc.poll() is None:
                if waited >= timeout_seconds:
                    timed_out = True
                    killed_by_us = True
                    break
                await asyncio.sleep(poll_interval)
                waited += poll_interval

            poll_result = proc.poll()

            if killed_by_us:
                # 我们主动杀死的进程（超时）
                try:
                    if proc.pid:
                        os.kill(proc.pid, signal.SIGTERM)
                        proc.wait(timeout=2)
                except (ProcessLookupError, subprocess.TimeoutExpired):
                    pass
                stdout_data = b""
                stderr_data = (
                    f"Execution timed out after {timeout_seconds} seconds"
                ).encode()
            elif poll_result is not None and poll_result != 0:
                # 进程被资源限制杀死（如 SIGKILL），读取剩余输出
                try:
                    stdout_data, stderr_data = proc.communicate(timeout=1)
                except subprocess.TimeoutExpired:
                    stdout_data, stderr_data = b"", b""
                timed_out = True  # 非正常退出，视为超时
            else:
                # 进程正常结束，读取输出
                try:
                    stdout_data, stderr_data = proc.communicate(timeout=1)
                except subprocess.TimeoutExpired:
                    stdout_data, stderr_data = b"", b""

            exit_code = proc.returncode if proc.returncode is not None else -1

            # 获取子进程资源使用（必须在 proc.wait() 之后调用）
            # RUSAGE_CHILDREN 是累计值，需要减去 baseline
            try:
                usage = resource.getrusage(resource.RUSAGE_CHILDREN)
                memory_kb = max(0, int(usage.ru_maxrss) - baseline_rss)
            except (OSError, AttributeError):
                memory_kb = None

        except FileNotFoundError:
            stdout_data = b""
            stderr_data = f"Interpreter not found: {cmd[0]}".encode()
            exit_code = 127
        except Exception as e:
            stdout_data = b""
            stderr_data = str(e).encode()
            exit_code = 1
        finally:
            if proc and proc.poll() is None:
                try:
                    proc.kill()
                    proc.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    pass

        end_time = time.perf_counter()
        execution_time_ms = int((end_time - start_time) * 1000)

        return SandboxResult(
            stdout=stdout_data.decode("utf-8", errors="replace"),
            stderr=stderr_data.decode("utf-8", errors="replace"),
            exit_code=exit_code,
            timed_out=timed_out,
            memory_kb=memory_kb,
            execution_time_ms=execution_time_ms,
        )

    def _set_limits(self) -> None:
        """在子进程中设置资源限制。"""
        for rlimit, (soft, hard) in self._get_limits():
            try:
                resource.setrlimit(rlimit, (soft, hard))
            except (ValueError, OSError):
                # 有些限制可能在某些平台上不支持
                pass

        # 忽略 SIGXCPU（CPU 时间超限后的礼貌终止信号）
        try:
            signal.signal(signal.SIGXCPU, signal.SIG_IGN)
        except (ValueError, OSError):
            pass

    # ── 便捷方法 ────────────────────────────────────────────────────────

    async def run_python(self, code: str) -> dict[str, Any]:
        """执行 Python 代码的便捷方法。"""
        return await self.execute(code, lang="python")

    async def run_javascript(self, code: str) -> dict[str, Any]:
        """执行 JavaScript 代码的便捷方法。"""
        return await self.execute(code, lang="javascript")

    async def run_bash(self, code: str) -> dict[str, Any]:
        """执行 Bash 脚本的便捷方法。"""
        return await self.execute(code, lang="bash")

    def get_limits(self) -> dict[str, Any]:
        """返回当前资源限制配置。"""
        return {
            "max_memory_mb": self._max_memory // (1024 * 1024),
            "max_cpu_seconds": self._max_cpu_seconds,
            "max_procs": self._max_procs,
        }
