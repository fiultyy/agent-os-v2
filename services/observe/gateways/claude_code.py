#!/usr/bin/env python3
"""Claude Code Gateway: tmux + stream-json driver.

桥接 claude code (stream-json 模式) 到 observe-service:
- tmux session 承载 claude code 进程
- 解析 stream-json 输出 → 统一 schema turn 事件
- 推 observe-service WS ingest
- 交互回路: observe /send → gateway → tmux claude code

技术决策(ADR-3 变更):
- stream-json 是 `-p` 一次性(单 prompt 单输出)。持续多轮不用 --resume(需要交互式输入)，
  改用"每轮启动新 claude -p 进程"方案。tmux session 做 session 承载/切换/attach backup view。
- tmux 管理: 用 libtmux (python-tmux 库)。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import websockets

# ── 配置 ─────────────────────────────────────────────────────

OBSERVE_WS_URL = os.getenv("OBSERVE_WS_URL", "ws://localhost:8002/ws/ingest")
HARNESS_TYPE = "claude-code"
PROJECT_ROOT = Path("/home/yy/projects/agent-os-v2")


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ── Stream-JSON 解析器 ────────────────────────────────────────

class StreamJSONParser:
    """解析 claude -p --output-format=stream-json 输出，映射到统一 schema。"""

    def __init__(self, harness_type: str, harness_id: str, session_id: str):
        self.harness_type = harness_type
        self.harness_id = harness_id
        self.session_id = session_id
        self.tick_id = str(uuid.uuid4())  # 每次 claude -p 是一个 tick

    def parse_line(self, line: str) -> Optional[Dict[str, Any]]:
        """解析单行 JSON，返回 observe-event 或 None。

        返回的字典必须包含 event_type 字段，否则返回 None。
        """
        try:
            data = json.loads(line.strip())
            msg_type = data.get("type")

            if msg_type == "assistant":
                # 助手消息，可能包含 tool_use (tool_call)
                parsed = self._parse_assistant(data)
            elif msg_type == "user":
                # 用户消息，可能包含 tool_result
                parsed = self._parse_user(data)
            elif msg_type == "result":
                # 最终结果 (tick_completed)
                parsed = self._parse_result(data)
            else:
                # 其他类型 (system, thinking_tokens) 忽略
                return None

            # 验证返回的字典包含 event_type
            if parsed and isinstance(parsed, dict) and "event_type" in parsed:
                return parsed
            return None

        except (json.JSONDecodeError, KeyError) as e:
            logger.debug(f"Skip non-JSON or malformed line: {e}")
            return None

    def _parse_assistant(self, data: Dict) -> Optional[Dict]:
        """解析 assistant 消息 (tool_call 事件)。"""
        message = data.get("message", {})
        content_blocks = message.get("content", [])

        for block in content_blocks:
            if block.get("type") == "tool_use":
                # 提取 tool_call
                tool_use_id = block.get("id")
                tool_name = block.get("name")
                tool_input = block.get("input", {})

                return {
                    "event_type": "tool_call",
                    "tick_id": self.tick_id,
                    "call_id": tool_use_id or str(uuid.uuid4()),
                    "tool_name": tool_name,
                    "arguments": tool_input,
                }

        return None

    def _parse_user(self, data: Dict) -> Optional[Dict]:
        """解析 user 消息 (tool_result 事件)。"""
        message = data.get("message", {})
        content_blocks = message.get("content", [])

        for block in content_blocks:
            if block.get("type") == "tool_result":
                # 提取 tool_result
                tool_use_id = block.get("tool_use_id")
                result_content = block.get("content", "")
                is_error = block.get("is_error", False)

                return {
                    "event_type": "tool_result",
                    "tick_id": self.tick_id,
                    "call_id": tool_use_id or "",
                    "result": result_content,
                    "error": str(result_content) if is_error else "",
                }

        return None

    def _parse_result(self, data: Dict) -> Optional[Dict]:
        """解析 result 消息 (tick_completed 事件)。"""
        return {
            "event_type": "tick_completed",
            "tick_id": self.tick_id,
            "status": "error" if data.get("is_error") else "success",
            "response": data.get("result", "")[:500],
            "duration_ms": data.get("duration_ms", 0),
        }


# ── Tmux Session 管理 ───────────────────────────────────────────

class TmuxSessionManager:
    """管理 tmux session (创建/删除/attach)。"""

    @staticmethod
    def session_name(session_id: str) -> str:
        """生成 tmux session 名称。"""
        return f"claude-observe-{session_id[:8]}"

    def create_session(self, session_id: str, project_dir: Path) -> str:
        """创建新 tmux session，返回 session 名称。"""
        name = self.session_name(session_id)

        # 检查是否已存在
        existing = subprocess.run(
            ["tmux", "has-session", "-t", name],
            capture_output=True,
        )
        if existing.returncode == 0:
            logger.info(f"Tmux session {name} already exists")
            return name

        # 创建新 session
        subprocess.run(
            ["tmux", "new-session", "-d", "-s", name, "-c", str(project_dir)],
            check=True,
        )
        logger.info(f"Created tmux session: {name}")
        return name

    def kill_session(self, session_id: str):
        """删除 tmux session。"""
        name = self.session_name(session_id)
        subprocess.run(["tmux", "kill-session", "-t", name])
        logger.info(f"Killed tmux session: {name}")

    def send_keys(self, session_id: str, keys: str):
        """向 tmux session 发送按键 (用于交互输入)。"""
        name = self.session_name(session_id)
        subprocess.run(
            ["tmux", "send-keys", "-t", name, keys, "C-m"],
            check=True,
        )


# ── Claude Code Gateway 主逻辑 ──────────────────────────────────

class ClaudeCodeGateway:
    """Claude Code Gateway: tmux + stream-json 驱动。"""

    def __init__(self):
        self.tmux = TmuxSessionManager()
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self.parser: Optional[StreamJSONParser] = None

    async def connect_observe(self, session_id: str):
        """连接 observe-service WS ingest。"""
        harness_id = self.tmux.session_name(session_id)
        query_params = f"harness_type={HARNESS_TYPE}&session_id={session_id}&harness_id={harness_id}"
        url = f"{OBSERVE_WS_URL}?{query_params}"

        self.ws = await websockets.connect(url)
        logger.info(f"Connected to observe-service: {HARNESS_TYPE}/{session_id}")

        # 初始化 parser
        self.parser = StreamJSONParser(HARNESS_TYPE, harness_id, session_id)

    async def disconnect_observe(self):
        """断开 observe-service 连接。"""
        if self.ws:
            await self.ws.close()
            logger.info("Disconnected from observe-service")

    def _build_observe_event(self, parsed: Dict) -> Dict:
        """将解析结果包装成 observe-event。"""
        base = {
            "event_id": str(uuid.uuid4()),
            "harness_type": self.parser.harness_type,
            "harness_id": self.parser.harness_id,
            "session_id": self.parser.session_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        event_type = parsed.pop("event_type")
        tick_id = parsed.pop("tick_id", self.parser.tick_id)

        return {
            "type": "event",
            "payload": {
                **base,
                "tick_id": tick_id,
                "event_type": event_type,
                "data": parsed,
            },
        }

    async def _emit_event(self, parsed: Dict):
        """发送事件到 observe-service。"""
        if not self.ws:
            logger.warning("WS not connected, skip event")
            return

        event = self._build_observe_event(parsed)
        await self.ws.send(json.dumps(event))
        logger.debug(f"Emitted event: {event['payload']['event_type']}")

    async def run_claude_turn(self, prompt: str):
        """运行一轮 claude code (claude -p + stream-json)。"""
        # 发送 tick_started
        await self._emit_event({
            "event_type": "tick_started",
            "tick_id": self.parser.tick_id,
            "request": prompt[:500],
        })

        # 构建 claude -p 命令
        cmd = [
            "claude",
            "-p",
            prompt,
            "--output-format",
            "stream-json",
            "--verbose",
        ]

        logger.info(f"Running claude -p: {prompt[:50]}...")

        # 启动进程，实时解析 stdout
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=PROJECT_ROOT,
        )

        # 逐行解析
        tool_count = 0
        for line in process.stdout:
            parsed = self.parser.parse_line(line)
            if parsed:
                event_type = parsed.get("event_type")
                await self._emit_event(parsed)
                if event_type == "tool_call":
                    tool_count += 1

        # 等待进程结束
        process.wait()

        # tick_already 在 parser._parse_result 发送，这里只记录
        logger.info(f"Claude -p completed: tick_id={self.parser.tick_id}, tool_count={tool_count}")

    async def interactive_loop(self, session_id: str, initial_prompt: str):
        """交互循环 (从 observe /send 接收消息)。"""
        await self.connect_observe(session_id)
        self.tmux.create_session(session_id, PROJECT_ROOT)

        # 初始 turn
        await self.run_claude_turn(initial_prompt)

        # TODO: 实现 observe /send 的轮询或 WS 接收
        # 当前 observe /send 是 stub，暂时只跑单轮

        await self.disconnect_observe()


# ── CLI 入口 ───────────────────────────────────────────────────

async def main():
    """CLI 入口: 启动 gateway 并运行一个 turn。"""
    import sys

    if len(sys.argv) < 2:
        print("Usage: python claude_code.py <session_id> <prompt>")
        sys.exit(1)

    session_id = sys.argv[1]
    prompt = " ".join(sys.argv[2:])

    gateway = ClaudeCodeGateway()
    try:
        await gateway.interactive_loop(session_id, prompt)
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    finally:
        # 清理 tmux session
        gateway.tmux.kill_session(session_id)


if __name__ == "__main__":
    asyncio.run(main())
