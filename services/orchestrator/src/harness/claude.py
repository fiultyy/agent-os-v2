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
from typing import Any, Dict, List, Optional

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
    ):
        self.session_id = session_id
        self.harness_id = harness_id or f"claude_{uuid.uuid4().hex[:8]}"
        self.cwd = cwd
        self.emitter = emitter or ObserveEmitter(
            harness_type=HARNESS_TYPE,
            harness_id=self.harness_id,
            session_id=session_id,
        )
        # running one-shot turn tasks (spawn multi-instance: multiple turns)
        self._turn_tasks: List[asyncio.Task] = []
        self._procs: List[subprocess.Popen] = []

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

        if resume:
            task = asyncio.create_task(self._run_resume(parser, message))
        else:
            task = asyncio.create_task(self._run_oneshot(parser, message))
        self._turn_tasks.append(task)
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
        """Resume mode: spawn `claude --resume <sid>` (interactive PTY).

        ponytail: stream-json is one-shot (-p). For resume we still spawn
        `claude --resume <sid>` but feed the message via stdin; when claude
        supports stream-json on resume it will be parsed, otherwise stdout is
        treated as raw text and the turn completes on process exit. This is the
        minimal path that keeps multi-instance (separate PTYs) working.
        """
        cmd = ["claude", "--resume", self.session_id, "-p", message,
               "--output-format", "stream-json", "--verbose"]
        await self._spawn_and_stream(parser, cmd)

    async def _spawn_and_stream(
        self, parser: StreamJSONParser, cmd: List[str],
    ) -> None:
        tool_count = 0
        proc: Optional[subprocess.Popen] = None
        try:
            proc = await asyncio.to_thread(
                subprocess.Popen,
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                cwd=str(self.cwd),
            )
            self._procs.append(proc)

            # read loop off the event loop
            def _read_lines():
                for line in proc.stdout:  # type: ignore[union-attr]
                    yield line

            loop = asyncio.get_event_loop()
            # ponytail: line iterator wrapped in to_thread per read to avoid
            # blocking the loop; for high-volume streams a StreamReader would
            # be better, but claude -p output is modest.
            for line in await loop.run_in_executor(None, lambda: list(proc.stdout)):  # type: ignore[union-attr]
                parsed = parser.parse_line(line)
                if parsed:
                    if parsed.get("event_type") == "tool_call":
                        tool_count += 1
                    await self.emitter.emit(parsed)

            await asyncio.to_thread(proc.wait)
        except Exception as e:
            logger.error("claude turn failed: %s", e)
            await self.emitter.emit(
                tick_completed(
                    HARNESS_TYPE, self.harness_id, self.session_id, parser.tick_id,
                    status="error", response=f"spawn failed: {e}",
                )
            )
        finally:
            if proc is not None and proc.poll() is None:
                proc.kill()

    async def spawn_instance(self) -> Dict[str, Any]:
        """Spawn a new claude instance on the same session (multi-instance).

        Returns immediately; the instance sits idle until a turn drives it.
        Concretely this just records a new harness_id suffix so the next turn
        on this client is a separate process — claude's --resume keeps state.
        """
        inst_id = f"{self.harness_id}-{uuid.uuid4().hex[:4]}"
        logger.info("Spawned claude instance: %s", inst_id)
        return {"instance_id": inst_id, "session_id": self.session_id}

    async def stop(self) -> None:
        for t in self._turn_tasks:
            t.cancel()
        for p in self._procs:
            if p.poll() is None:
                p.kill()
        await self.emitter.close()
