"""Claude Code harness client (orchestrator-side).

PTY/subprocess spawn of `claude`. Two modes:
- resume: `claude --resume <sid>` (interactive PTY, attach to an existing
  session; multi-instance = multiple PTYs on the same resume id, no lock)
- one-shot: `claude -p <prompt> --output-format stream-json --verbose`

stream-json output is parsed line-by-line into ObserveEvent dicts
(tick_started/tool_call/tool_result/tick_completed) and pushed to the
ObserveEmitter. Parser ported from services/observe/gateways/claude_code.py.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .events import (
    tick_completed,
    tick_started,
    tool_call,
    tool_result,
)
from .emit import ObserveEmitter

logger = logging.getLogger(__name__)

HARNESS_TYPE = "claude-code"
DEFAULT_CWD = Path(
    os.getenv("CLAUDE_CWD", "/home/yy/projects/agent-os-v2")
)


# ── stream-json parser ────────────────────────────────────────────────

class StreamJSONParser:
    """Parse `claude -p --output-format=stream-json` lines → ObserveEvent dicts.

    One claude -p run = one tick. tick_id fixed at construction.
    """

    def __init__(self, harness_id: str, session_id: str, tick_id: str = ""):
        self.harness_id = harness_id
        self.session_id = session_id
        self.tick_id = tick_id or str(uuid.uuid4())
        # session_id captured from the result event (fork uses this)
        self.captured_sid: Optional[str] = None
        # result 事件已发 tick_completed(proc 退无 result 时用于兜底闭环)
        self.completed: bool = False

    def parse_line(self, line: str) -> Optional[Dict[str, Any]]:
        try:
            data = json.loads(line.strip())
        except (json.JSONDecodeError, ValueError):
            return None

        msg_type = data.get("type")
        if msg_type == "assistant":
            return self._parse_assistant(data)
        if msg_type == "user":
            return self._parse_user(data)
        if msg_type == "result":
            return self._parse_result(data)
        return None

    def _parse_assistant(self, data: Dict) -> Optional[Dict[str, Any]]:
        for block in data.get("message", {}).get("content", []) or []:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                return tool_call(
                    HARNESS_TYPE, self.harness_id, self.session_id, self.tick_id,
                    tool_name=block.get("name", ""),
                    arguments=block.get("input", {}) or {},
                    call_id=block.get("id") or str(uuid.uuid4()),
                )
        return None

    def _parse_user(self, data: Dict) -> Optional[Dict[str, Any]]:
        for block in data.get("message", {}).get("content", []) or []:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                content = block.get("content", "")
                is_error = block.get("is_error", False)
                return tool_result(
                    HARNESS_TYPE, self.harness_id, self.session_id, self.tick_id,
                    call_id=block.get("tool_use_id") or "",
                    result=content,
                    error=str(content) if is_error else "",
                )
        return None

    def _parse_result(self, data: Dict) -> Optional[Dict[str, Any]]:
        # capture session_id emitted in the terminal result event (used by fork)
        sid = data.get("session_id")
        if sid:
            self.captured_sid = sid
        self.completed = True
        return tick_completed(
            HARNESS_TYPE, self.harness_id, self.session_id, self.tick_id,
            status="error" if data.get("is_error") else "success",
            response=data.get("result", ""),
            duration_ms=data.get("duration_ms", 0),
        )


# ── Claude Code client ────────────────────────────────────────────────

class ClaudeClient:
    """Spawns claude (resume or one-shot), parses stream-json, emits events.

    Per ADR-4: orchestrator is the ONLY client that spawns claude. observe
    never connects to claude — it only receives what we push.
    """

    def __init__(
        self,
        session_id: str,
        harness_id: str = "",
        cwd: Path = DEFAULT_CWD,
        emitter: Optional[ObserveEmitter] = None,
        native_sid: Optional[str] = None,
        on_native_sid: Optional[Callable[[str], Any]] = None,
    ):
        self.session_id = session_id
        self.harness_id = harness_id or f"claude_{uuid.uuid4().hex[:8]}"
        self.cwd = cwd
        self.emitter = emitter or ObserveEmitter(
            harness_type=HARNESS_TYPE,
            harness_id=self.harness_id,
            session_id=session_id,
        )
        # native harness session id(claude 完整 UUID)。首 turn(oneshot)前为 None,
        # turn 完成后从 stream-json result 事件回填;后续 turn 用它 `claude --resume`
        # 续聊原生 session(session_id 是 orche 的 ext 12-hex 句柄,claude 不认)。
        self.native_sid = native_sid
        # native_sid 回填回调(routes 注入 → 落 OrchSessionStore 持久层)
        self.on_native_sid = on_native_sid
        # running one-shot turn tasks (spawn multi-instance: multiple turns)
        self._turn_tasks: List[asyncio.Task] = []
        # asyncio.subprocess.Process(create_subprocess_exec,异步流式)
        self._procs: List[Any] = []
        # per-client turn 串行化:防并发 turn 在 native_sid 回填前都判 oneshot →
        # 建多个原生 session 互覆盖丢上下文(方案 B+C ext→native 1:1 契约)。
        self._turn_lock = asyncio.Lock()

    async def connect(self) -> bool:
        """Open the observe ingest WS only (no persistent harness connection).

        Claude is subprocess-per-turn, so there's no long-lived connection to
        establish up front — connect() just registers with observe.
        """
        return await self.emitter.connect()

    async def turn(
        self, message: str, resume: bool = False,
    ) -> Dict[str, Any]:
        """Run one claude turn.

        resume=True  → `claude --resume <sid>` then send the message (PTY path,
                       multi-instance safe via separate processes).
        resume=False → `claude -p <message> --output-format stream-json` (one-shot,
                       stream-json parsed into events).

        Returns {tick_id, status}. The stream is consumed in a background task
        so the route can return immediately (turn events stream to observe).
        """
        parser = StreamJSONParser(self.harness_id, self.session_id)
        tick_id = parser.tick_id

        # emit tick_started up front (we know the request)
        await self.emitter.emit(
            tick_started(HARNESS_TYPE, self.harness_id, self.session_id,
                         tick_id=tick_id, request=message)
        )

        async def _run_and_capture() -> None:
            # per-client 串行:do_resume 决策(读 native_sid)+ run + 回填全在锁内,
            # 防并发 turn 都判 oneshot 建多原生 session 互覆盖丢上下文。
            # native_sid 缺失时强制 oneshot:拿 ext 12-hex 去 `claude --resume` 必 not found。
            async with self._turn_lock:
                do_resume = resume and bool(self.native_sid)
                if do_resume:
                    await self._run_resume(parser, message)
                else:
                    await self._run_oneshot(parser, message)
                # 回填 native_sid:oneshot/resume 的 result 事件都携带原生 session_id;
                # 落内存 + 通知 routes 持久化(回调 fire-and-forget,失败不阻塞 turn)。
                if parser.captured_sid and parser.captured_sid != self.native_sid:
                    self.native_sid = parser.captured_sid
                    if self.on_native_sid:
                        try:
                            self.on_native_sid(parser.captured_sid)
                        except Exception:
                            logger.warning("on_native_sid callback failed", exc_info=True)

        task = asyncio.create_task(_run_and_capture())
        self._turn_tasks.append(task)
        # 完成后移除引用,允许 task GC(避免 _turn_tasks 无界增长持有已完成 frame)
        task.add_done_callback(self._turn_tasks.remove)
        return {"tick_id": tick_id, "status": "started"}

    async def _run_oneshot(self, parser: StreamJSONParser, message: str) -> None:
        """One-shot: claude -p <message> --output-format stream-json."""
        cmd = [
            "claude", "-p", message,
            "--output-format", "stream-json",
            "--verbose",
        ]
        logger.info("claude -p (tick=%s): %s...", parser.tick_id, message[:50])
        await self._spawn_and_stream(parser, cmd)

    async def _run_resume(self, parser: StreamJSONParser, message: str) -> None:
        """Resume mode: `claude --resume <native_sid> -p <message>`.

        用 native_sid(原生 claude UUID)续聊,不是 ext session_id(12-hex 句柄,
        claude 不认 → not found)。do_resume 保证 native_sid 非 None。stream-json
        result 事件回带 session_id,_run_and_capture 据此保持 native_sid 同步
        (fork 后 native 变化也覆盖)。
        """
        cmd = ["claude", "--resume", self.native_sid or "", "-p", message,
               "--output-format", "stream-json", "--verbose"]
        await self._spawn_and_stream(parser, cmd)

    async def _spawn_and_stream(
        self, parser: StreamJSONParser, cmd: List[str],
    ) -> None:
        # 原生 async subprocess + 逐行流式(替代 Popen + list(stdout) 全量读):
        # stream-json 事件实时 emit(不再批量到 EOF);wait_for 超时防 proc 挂起阻塞。
        proc: Optional[asyncio.subprocess.Process] = None
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=str(self.cwd),
            )
            self._procs.append(proc)

            # 逐行流式解析 emit(tool_call/tool_result/tick_completed 实时到 observe)
            assert proc.stdout is not None
            async for raw in proc.stdout:
                line = raw.decode(errors="replace") if isinstance(raw, (bytes, bytearray)) else raw
                parsed = parser.parse_line(line)
                if parsed:
                    await self.emitter.emit(parsed)

            # 等 proc 退出(带超时,防 runaway tool / claude 挂起无限阻塞 executor)
            await asyncio.wait_for(proc.wait(), timeout=300)
        except asyncio.TimeoutError:
            logger.error("claude turn timed out (300s), killing")
            if proc is not None and proc.returncode is None:
                proc.kill()
        except Exception as e:
            logger.error("claude turn failed: %s", e)
        finally:
            if proc is not None and proc.returncode is None:
                proc.kill()
            # tick 闭环兜底:proc 退但无 result 事件(OOM/崩溃/非JSON stderr 合并丢弃)
            # → 补发 error tick_completed,保证每个 tick_started 都有 completed(防前端永转)
            if not parser.completed:
                await self.emitter.emit(
                    tick_completed(
                        HARNESS_TYPE, self.harness_id, self.session_id, parser.tick_id,
                        status="error", response="claude exited without result event",
                    )
                )
            # _procs 清理(与 _turn_tasks 同形,避免无界增长持有已结束 Process)
            if proc is not None and proc in self._procs:
                self._procs.remove(proc)

    async def spawn_instance(self) -> Dict[str, Any]:
        """Spawn a new claude instance on the same session (multi-instance).

        Returns immediately; the instance sits idle until a turn drives it.
        Concretely this just records a new harness_id suffix so the next turn
        on this client is a separate process — claude's --resume keeps state.
        """
        inst_id = f"{self.harness_id}-{uuid.uuid4().hex[:4]}"
        logger.info("Spawned claude instance: %s", inst_id)
        return {"instance_id": inst_id, "session_id": self.session_id}

    async def fork(self, orig_sid: str, first_msg: str) -> Dict[str, Any]:
        """Fork an existing claude session and run the first turn.

        `claude --resume <orig_sid> --fork-session -p <first_msg> ...` opens a
        NEW session id carrying orig_sid's context, then runs first_msg as a
        one-shot turn. The new session_id is captured from the stream-json
        result event.

        Blocks until the forked turn completes (unlike turn() which streams in
        the background) because the caller needs the new_sid to drive it next.
        Returns {new_sid, tick_id, status}.
        """
        new_sid_fallback = str(uuid.uuid4())
        parser = StreamJSONParser(
            self.harness_id, orig_sid, tick_id=new_sid_fallback,
        )

        await self.emitter.emit(
            tick_started(HARNESS_TYPE, self.harness_id, orig_sid,
                         tick_id=parser.tick_id, request=first_msg)
        )

        cmd = [
            "claude", "--resume", orig_sid, "--fork-session",
            "-p", first_msg,
            "--output-format", "stream-json",
            "--verbose",
        ]
        logger.info(
            "claude fork (tick=%s, from=%s): %s...",
            parser.tick_id, orig_sid, first_msg[:50],
        )
        await self._spawn_and_stream(parser, cmd)

        # _parse_result captured the result event's session_id if present。
        # captured_sid 缺失(fork 子进程出错/无 result)→ new_sid=None,绝不用伪 UUID
        # 占位(否则落 store 当 native_sid,下个 turn `claude --resume <伪UUID>` 必败)。
        # routes.fork_session 的 `if not new_sid: raise 500` 会拦住 None。
        new_sid = parser.captured_sid
        if not new_sid:
            logger.warning("fork: no session_id in stream-json result → returning None")
        return {"new_sid": new_sid, "tick_id": parser.tick_id,
                "status": "forked" if new_sid else "failed"}

    async def delete(self, sid: str = None) -> Dict[str, Any]:
        """Delete a claude session transcript (jsonl) and stop the client.

        The transcript lives at
        ~/.claude/projects/<slug>/<sid>.jsonl where slug = cwd with "/" → "-".
        A missing file is treated as already-deleted (deleted=True). Then the
        client is stopped (processes cancelled, observe WS closed).
        """
        # transcript 文件名是 native UUID(不是 ext 12-hex);native 优先。
        # native None(首 turn 前无 transcript)→ fallback sid → 路径不存在 →
        # deleted=True("already gone"),语义正确(从未建过)。
        target_sid = self.native_sid or sid or self.session_id
        slug = str(self.cwd).replace("/", "-")
        transcript = Path.home() / ".claude" / "projects" / slug / f"{target_sid}.jsonl"
        deleted = False
        try:
            if os.path.exists(transcript):
                os.remove(transcript)
                deleted = True
            else:
                deleted = True  # already gone
        except Exception as e:
            logger.error("delete: failed to remove %s: %s", transcript, e)
            return {"deleted": False, "sid": target_sid, "error": str(e)}

        await self.stop()
        return {"deleted": deleted, "sid": target_sid}

    async def stop(self) -> None:
        for t in self._turn_tasks:
            t.cancel()
        for p in self._procs:
            if p.returncode is None:
                p.kill()
        await self.emitter.close()
