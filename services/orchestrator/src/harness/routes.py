"""Primitive API routes — the thin orchestration layer (spec section 4).

Six endpoints over {claw, claude-code}:
- POST   /h/{type}/sessions              create session
- GET    /h/{type}/sessions              list sessions
- POST   /h/{type}/sessions/{id}/turn    trigger turn (core primitive)
- POST   /h/{type}/sessions/{id}/spawn   spawn instance (claude multi-instance)
- DELETE /h/{type}/sessions/{id}         close session
- POST   /switch                         switch active session (focus, not lock)

No locks (ADR-5): concurrency is delegated to the harness (claw gateway handles
multi-session; claude multi-PTY --resume). orchestrator only routes + observes.

Per ADR-4: orchestrator is the ONLY harness client. Each route drives the
harness client (openclaw send_message / claude spawn), and the client
background-connects + maps events → observe /ws/ingest.
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .openclaw import OpenClawClient
from .claude import ClaudeClient

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/h", tags=["harness"])

VALID_TYPES = {"claw", "claude-code", "agent-os-v2"}

# observe-service REST base (sessions are persisted in SQLite there; the TUI's
# source of truth). orche delete must sync here or the count drifts.
OBSERVE_REST_URL = os.getenv("OBSERVE_URL", "http://localhost:8002")  # 容器部署 compose 注入 OBSERVE_URL=http://observe:8002

# routes use "claw" as the harness key, but the openclaw client registers with
# observe under harness_type="openclaw". Map so the DELETE hits the right row.
_OBSERVE_HARNESS_TYPE = {
    "claw": "openclaw", "claude-code": "claude-code", "agent-os-v2": "agent-os-v2",
}


async def _observe_delete_session(harness_type: str, session_id: str) -> bool:
    """Best-effort DELETE of a session record from observe-service.

    Non-fatal: if observe is unreachable the orche delete still succeeds.
    Runs the blocking http call off the event loop via asyncio.to_thread.
    """
    ob_type = _OBSERVE_HARNESS_TYPE.get(harness_type, harness_type)
    url = f"{OBSERVE_REST_URL}/sessions/{ob_type}/{session_id}"

    def _do_delete() -> bool:
        import urllib.request
        try:
            req = urllib.request.Request(url, method="DELETE")
            with urllib.request.urlopen(req, timeout=3) as resp:
                return 200 <= resp.status < 300
        except Exception as e:
            logger.warning("observe session delete failed (%s/%s): %s",
                           ob_type, session_id, e)
            return False

    return await asyncio.to_thread(_do_delete)

# active session (frontend focus = send target, NOT a lock)
_active: Dict[str, str] = {"type": "", "id": ""}


# ── session registry ──────────────────────────────────────────────────
# keyed by (harness_type, session_id). Holds the live client + metadata.
# _store is the persistent mirror (ext→native mapping); _sessions holds the
# live client. Restart rebuilds _sessions from _store (restore_all_sessions).
_sessions: Dict[str, Dict[str, Any]] = {}

# orche 自管持久层(方案 B+C):不持对话内容,只存 ext→native 映射 + 元数据。
from .session_store import OrchSessionStore
_store = OrchSessionStore()


def _key(harness_type: str, session_id: str) -> str:
    return f"{harness_type}:{session_id}"


def _get_client(harness_type: str, session_id: str) -> Optional[Any]:
    rec = _sessions.get(_key(harness_type, session_id))
    return rec["client"] if rec else None


async def _ensure_client(harness_type: str, session_id: str) -> Optional[Any]:
    """获取 client;内存无但 store 有(restore 失败/未重建的孤儿)→ 按需重建(惰性)。

    解决 store-only session 在 turn/archive/spawn/fork 恒 404(架构完整性);
    claw 路径即惰性重连(healthcheck 的 routes 侧)。重建失败返 None → 调用方 404。
    """
    client = _get_client(harness_type, session_id)
    if client is not None:
        return client
    row = _store.get(harness_type, session_id)
    if row is None:
        return None
    try:
        if harness_type == "claude-code":
            client = await _create_claude(session_id, row.get("cwd"), native_sid=row.get("native_sid"))
            _sessions[_key(harness_type, session_id)] = {
                "client": client, "session_id": session_id, "harness_type": harness_type,
                "agent_id": None, "native_sid": row.get("native_sid"), "cwd": row.get("cwd"),
            }
        else:  # claw 惰性重连
            client = await _create_claw(session_id, row.get("agent_id"))
            _sessions[_key(harness_type, session_id)] = {
                "client": client, "session_id": session_id, "harness_type": harness_type,
                "agent_id": row.get("agent_id"),
                "native_sid": row.get("native_sid") or session_id, "cwd": None,
            }
        logger.info("lazy-restored session %s/%s", harness_type, session_id)
        return client
    except Exception as e:
        logger.warning("lazy restore failed (%s/%s): %s", harness_type, session_id, e)
        return None


# ── request models ────────────────────────────────────────────────────

class CreateSessionReq(BaseModel):
    agent_id: Optional[str] = None   # claw agent key (e.g. agent:main:main)
    cwd: Optional[str] = None        # claude working directory


class TurnReq(BaseModel):
    message: str
    agent_id: Optional[str] = None   # claw agentId override
    thinking: Optional[str] = None   # claw thinking param
    resume: bool = False             # claude --resume path


class SwitchReq(BaseModel):
    type: str
    id: str


# ── helpers ───────────────────────────────────────────────────────────

def _validate_type(harness_type: str) -> None:
    if harness_type not in VALID_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid type '{harness_type}'. Must be one of {sorted(VALID_TYPES)}",
        )


async def _create_claw(session_id: str, agent_id: Optional[str]) -> OpenClawClient:
    client = OpenClawClient(session_key=session_id or agent_id or "agent:main:main")
    client.start_background()
    return client


def _agent_from_key(session_id: str) -> str:
    """claw session_key `agent:<agent>:<conv>` → <agent>;非标准格式 → session_id 本身。"""
    parts = session_id.split(":")
    if len(parts) >= 3 and parts[0] == "agent":
        return parts[1]
    return session_id


async def _reconnect_claw(session_id: str) -> Optional[OpenClawClient]:
    """惰性重连/创建:store 有→重建死/stale client;store 无(observe-only,gateway
    有但或che未记录)→ 推断 agent + _create_claw + 落库。

    手动 reconnect 端点 + trigger_turn 自动恢复共用。OpenClawClient WS 断后
    running 永久 False(connect() 一次性循环无重连),或 stale(send 死键置)→
    本 helper 换新 client 重建连接。返 client(running 真/假),None=_create_claw 异常。
    """
    row = _store.get("claw", session_id)
    if row is not None:
        agent_id = row.get("agent_id")
        native_sid = row.get("native_sid") or session_id
    else:
        # observe-only session:gateway 存在但或che store 无 → 推断 agent 惰性建
        agent_id = _agent_from_key(session_id)
        native_sid = session_id
    _sessions.pop(_key("claw", session_id), None)
    try:
        client = await _create_claw(session_id, agent_id)
    except Exception as e:
        logger.warning("reconnect _create_claw failed (%s): %s", session_id, e)
        return None
    for _ in range(50):
        if getattr(client, "running", False):
            break
        await asyncio.sleep(0.04)
    if row is None:
        _store.create(session_id, "claw", native_sid=native_sid, agent_id=agent_id)
    _sessions[_key("claw", session_id)] = {
        "client": client, "session_id": session_id, "harness_type": "claw",
        "agent_id": agent_id, "native_sid": native_sid, "cwd": None,
    }
    return client


async def _create_claude(
    session_id: str, cwd: Optional[str], native_sid: Optional[str] = None,
) -> ClaudeClient:
    """Build a ClaudeClient + wire the native_sid backfill → persistent store.

    native_sid passed on restore (rebuild); None on fresh create (first turn
    fills it). on_native_sid closes the loop: claude turn captures the native
    UUID from stream-json → store.update_native_sid → 内存记录同步。
    """
    from pathlib import Path
    from .claude import DEFAULT_CWD

    def _on_native(nsid: str) -> None:
        _store.update_native_sid(session_id, nsid)
        rec = _sessions.get(_key("claude-code", session_id))
        if rec is not None:
            rec["native_sid"] = nsid

    client = ClaudeClient(
        session_id=session_id,
        cwd=Path(cwd) if cwd else DEFAULT_CWD,
        native_sid=native_sid,
        on_native_sid=_on_native,
    )
    await client.connect()
    return client


def _load_native_messages(session_id: str) -> list:
    """Deserialize persisted ModelMessages for restart-safe recall (None/坏 → [])."""
    from pydantic_ai.messages import ModelMessagesTypeAdapter
    raw = _store.load_messages(session_id)
    if not raw:
        return []
    try:
        return ModelMessagesTypeAdapter.validate_json(raw)
    except Exception:
        logger.warning("native messages decode failed (%s), starting fresh", session_id)
        return []


def _persist_native_messages(session_id: str, messages: list) -> None:
    """Serialize ModelMessages after a turn (best-effort; ADR-7 不破主路径)."""
    from pydantic_ai.messages import ModelMessagesTypeAdapter
    try:
        _store.save_messages(
            session_id, ModelMessagesTypeAdapter.dump_json(messages).decode()
        )
    except Exception:
        logger.warning("native messages persist failed (%s)", session_id)


_memory_tools: tuple | None = None  # 惰性单例 (ExperienceTool, KGMemoryTool)


def _get_memory_tools() -> tuple | None:
    """惰性构造 ExperienceTool + KGMemoryTool 单例(避免 per-session executor 泄漏)。

    依赖 _state.knowledge_graph;None 或构造失败 → None(MemoryCapability 不注入,
    ADR-7)。首次调用构造,后续复用缓存(executor/conn 跨 session 共享)。
    """
    global _memory_tools
    if _memory_tools is not None:
        return _memory_tools
    from src.services import _state
    if _state.knowledge_graph is None:
        return None
    try:
        from src.memory.experience_kg import ExperienceKG
        from src.memory.kg_query_interface import KGQueryInterface
        from src.memory.tools.experience_tool import ExperienceTool
        from src.memory.tools.kg_memory_tool import KGMemoryTool
        _memory_tools = (
            ExperienceTool(ExperienceKG()),
            KGMemoryTool(KGQueryInterface(_state.knowledge_graph)),
        )
        return _memory_tools
    except Exception:
        logger.warning("memory tools construct failed; MemoryCapability not injected")
        return None


async def _build_native_session(
    session_id: str, messages: list | None = None,
) -> Dict[str, Any]:
    """agent-os-v2 native in-process session:pydantic-ai Agent + ObserveEmitter + 消息历史。

    ADR pydantic-ai-v2-adoption P8:native turn 走 /h/agent-os-v2。capabilities 注入
    ObserveCapability(真 emitter→observe)+ GuardrailCapability(护 native tool)。
    memory(P3 recall,defer_loading)+ skill 已通电;profile YAGNI(native 用扁平
    instructions=system_prompt,分层 ProfileRegistry 未装配,ADR)。
    """
    from .native_agent import HARNESS_TYPE, build_native_agent
    from .capabilities import (
        GuardrailCapability,
        MemoryCapability,
        MemoryWriterCapability,
        ObserveCapability,
        ToolBridgeCapability,
        make_profile_capabilities,
        make_skill_capabilities,
    )
    from .emit import ObserveEmitter
    from src.services import _state
    from src.skills.skill_loader import SkillLoader
    from src.tools.guardrail import Guardrail

    harness_id = f"native_{session_id[:8]}"
    emitter = ObserveEmitter(HARNESS_TYPE, harness_id=harness_id, session_id=session_id)
    try:
        await emitter.connect()  # best-effort(observe 断不影响 native run,ADR-7)
    except Exception:
        logger.warning("native emitter connect failed (%s)", harness_id)
    try:
        skill_caps = make_skill_capabilities(SkillLoader())  # 扫 SKILL.md(defer 披露)
    except Exception:
        skill_caps = []  # 扫描失败不阻塞 native(P6 孤岛通电 best-effort)
    caps = [
        ObserveCapability(emitter=emitter, harness_id=harness_id, session_id=session_id),
        # P5 MemoryWriter(写侧,自动沉淀):每轮 tool_result + 用户轮结束四件套。
        # env gate:memory_event_bus/knowledge_graph None → no-op(ADR-7)。
        MemoryWriterCapability(
            memory_event_bus=_state.memory_event_bus,
            knowledge_graph=_state.knowledge_graph,
            agent_id=session_id,
            session_id=session_id,
        ),
        # P8 ToolBridge:v2 ToolRegistry → execute_tool dispatch(模型经它调 v2 tool)。
        # P7 pitfail:tool_executor 返 {status:'error'} 非 raise → wrapper 内显式计数。
        ToolBridgeCapability(
            tool_executor=_state.tool_executor,
            pitfail_registry=_state.pitfail_registry,
        ),
        GuardrailCapability(guardrail=Guardrail()),
        *skill_caps,
    ]
    # P3 MemoryCapability(recall 读侧,defer_loading):模型 load_capability('memory')
    # 后见 experience_memory + kg_memory tool。glm 是否触发 load 需 e2e 验证(ADR)。
    mt = _get_memory_tools()
    if mt is not None:
        caps.append(MemoryCapability(experience_tool=mt[0], kg_tool=mt[1]))
    # ADR-1: native profile 分层 capa 化(AGENTS.md L1+L2 → system prompt)。
    # _state.profile_registry None(env/启动失败降级)→ make_profile_capabilities 返 []。
    caps.extend(make_profile_capabilities(
        _state.profile_registry.get("native") if _state.profile_registry else None
    ))
    agent = build_native_agent(capabilities=caps)
    return {
        "agent": agent, "emitter": emitter, "messages": messages or [],
        "session_id": session_id, "harness_type": "agent-os-v2",
        "native_sid": session_id,
    }


# ── routes ────────────────────────────────────────────────────────────

@router.post("/{harness_type}/sessions")
async def create_session(
    harness_type: str, req: CreateSessionReq,
) -> Dict[str, Any]:
    _validate_type(harness_type)
    if harness_type == "agent-os-v2":
        session_id = str(uuid.uuid4().hex[:12])
        key = _key(harness_type, session_id)
        if key in _sessions:
            return {"session_id": session_id, "type": harness_type, "status": "exists"}
        _sessions[key] = await _build_native_session(session_id)
        _store.create(session_id, "agent-os-v2", native_sid=session_id)
        return {"session_id": session_id, "type": harness_type, "status": "created"}
    if harness_type == "claw":
        # claw gateway session_key 必须是 claw 格式 agent:<agent>:<conv>;或che session_id = claw key
        # (统一,前端查 observe 同 key;uuid hex claw 不认 → subscribe/send 到不存在 session → 0 events)
        agent = req.agent_id or "main"
        session_id = agent if ":" in agent else f"agent:{agent}:main"
    else:
        session_id = str(uuid.uuid4().hex[:12])

    # ADR-4:同 session_id 复用 client,防重复订阅(多 OpenClawClient 连同 claw session 抢事件)
    key = _key(harness_type, session_id)
    if key in _sessions:
        # claw 长连 client 可能已死(running=False 且 connect task 已结束 = 重连失败)→
        # 重建,避免僵尸死锁(turn 503 + create 同 key 永远短路)。claude client 无状态,
        # exists 短路正确。ponytail:更彻底是 trigger_turn 惰性重连 / 后台 healthcheck。
        if harness_type == "claw":
            old = _sessions[key].get("client")
            conn = getattr(old, "_connect_task", None)
            if (getattr(old, "running", True) is False
                    and conn is not None and getattr(conn, "done", lambda: False)()):
                _sessions.pop(key, None)   # 死 client,落到下面重建
            else:
                return {"session_id": session_id, "type": harness_type, "status": "exists"}
        else:
            return {"session_id": session_id, "type": harness_type, "status": "exists"}

    client = await (_create_claw(session_id, req.agent_id) if harness_type == "claw"
                    else _create_claude(session_id, req.cwd))
    # claw native = session_key(= ext);claude native 首 turn 后回填(None)
    native_sid = session_id if harness_type == "claw" else None
    _sessions[key] = {
        "client": client,
        "session_id": session_id,
        "harness_type": harness_type,
        "agent_id": req.agent_id,
        "native_sid": native_sid,
        "cwd": req.cwd,
    }
    # 持久层落库(重启不丢)
    if harness_type == "claw":
        _store.create(session_id, "claw", native_sid=session_id, agent_id=req.agent_id)
        # 等 gateway 连上 + 建 gateway session entry(非 main conv gateway 不自动建,
        # send 会 session not found → 503;main conv 幂等)。reconnect 路径不调(已有 entry)。
        for _ in range(50):
            if getattr(client, "running", False):
                break
            await asyncio.sleep(0.04)
        if getattr(client, "running", False):
            # 建 gateway session entry(create_session 内部已 try/except + log,这里再兜防
            # 测试 mock client 非 async;失败不阻塞 create——turn 时若 not found 会惰性重连)。
            cs = getattr(client, "create_session", None)
            if cs is not None:
                try:
                    await cs()
                except Exception:
                    pass
    else:
        _store.create(session_id, "claude-code", native_sid=None, cwd=req.cwd)
    return {"session_id": session_id, "type": harness_type, "status": "created"}


@router.get("/{harness_type}/sessions")
async def list_sessions(harness_type: str) -> Dict[str, Any]:
    _validate_type(harness_type)
    # 持久层为准(重启后内存空但 store 在);合并内存 client 运行状态(claw 长连)
    items = []
    for r in _store.list_all(harness_type):
        rec = _sessions.get(_key(harness_type, r["ext_id"]))
        client = rec.get("client") if rec else None
        if client is not None and not hasattr(client, "running"):
            # claude-code subprocess-per-turn:无长连,"running" = 有在飞 turn task
            running = any(not t.done() for t in getattr(client, "_turn_tasks", []))
        else:
            running = bool(client and getattr(client, "running", False))
        items.append({
            "session_id": r["ext_id"],
            "native_sid": r.get("native_sid"),
            "agent_id": r.get("agent_id"),
            "cwd": r.get("cwd"),
            "running": running,
            "last_turn_at": r.get("last_turn_at"),
        })
    return {"type": harness_type, "sessions": items, "count": len(items)}


@router.post("/{harness_type}/sessions/{session_id}/turn")
async def trigger_turn(
    harness_type: str, session_id: str, req: TurnReq,
) -> Dict[str, Any]:
    _validate_type(harness_type)
    if harness_type == "agent-os-v2":
        rec = _sessions.get(_key(harness_type, session_id))
        if rec is None:
            # store 有但内存无(restore 孤儿)→ 重建 native agent + 回填持久化 message_history
            if _store.get(harness_type, session_id) is None:
                raise HTTPException(status_code=404, detail="session not found")
            rec = await _build_native_session(
                session_id, messages=_load_native_messages(session_id)
            )
            _sessions[_key(harness_type, session_id)] = rec
        # in-process Agent run:ObserveCapability 自动推 observe,guardrail 自动护
        result = await rec["agent"].run(req.message, message_history=rec["messages"])
        rec["messages"] = result.all_messages()
        _store.touch(session_id)
        _persist_native_messages(session_id, rec["messages"])  # 续聊持久化(重启不丢)
        return {"session_id": session_id, "status": "completed",
                "response": result.output}
    client = await _ensure_client(harness_type, session_id)
    if client is None:
        raise HTTPException(status_code=404, detail="session not found")

    if harness_type == "claw":
        # WS 断(running=False)或 stale 残留 → 惰性重连(不再直接 503 卡死)。
        # reconnect 后仍 running=False = gateway 真不可达 → 503。
        if not getattr(client, "running", False) or getattr(client, "stale", False):
            client = await _reconnect_claw(session_id)
            if client is None:
                raise HTTPException(status_code=404, detail="session not found")
            if not getattr(client, "running", False):
                raise HTTPException(
                    status_code=503,
                    detail="claw reconnect failed; gateway :18789 down?",
                )
        await client.send_message(req.message, agent_id=req.agent_id, thinking=req.thinking)
        _store.touch(session_id)
        # 本次 send 检测到死 key(reconnect 后仍死)→ 503,让调用方 delete+recreate。
        # send_message 已 emit error tick(前端停转)。
        if getattr(client, "stale", False):
            raise HTTPException(
                status_code=503,
                detail="claw session dead (session not found), delete + recreate",
            )
        return {"session_id": session_id, "status": "sent", "message": req.message[:50]}
    else:  # claude-code
        # 自动 resume:有 native_sid(原生 UUID)→ 续聊原生 session;无(首 turn)→
        # oneshot 建原生 session 并捕获 id。多轮能力自动恢复(不再每次 oneshot 丢上下文)。
        resume = req.resume or client.native_sid is not None
        result = await client.turn(req.message, resume=resume)
        _store.touch(session_id)
        return {"session_id": session_id, "status": result["status"],
                "tick_id": result["tick_id"]}


@router.post("/{harness_type}/sessions/{session_id}/reconnect")
async def reconnect_session(
    harness_type: str, session_id: str,
) -> Dict[str, Any]:
    """claw 手动重连:清死 client 重建,短轮询等 running。

    OpenClawClient WS 断后 running 永久 False(connect() 一次性循环无重连)→
    trigger_turn 卡 503。本端点让前端一键拉起重连。
    claude-code 无状态(子进程按 turn spawn),重连无意义 → 400。
    """
    _validate_type(harness_type)
    if harness_type != "claw":
        raise HTTPException(
            status_code=400,
            detail="reconnect only for claw (claude-code is stateless)",
        )
    client = await _reconnect_claw(session_id)
    if client is None:
        raise HTTPException(status_code=404, detail="session not found")
    return {"session_id": session_id, "connected": bool(getattr(client, "running", False))}


@router.post("/{harness_type}/sessions/{session_id}/spawn")
async def spawn_instance(
    harness_type: str, session_id: str,
) -> Dict[str, Any]:
    _validate_type(harness_type)
    if harness_type == "agent-os-v2":
        raise HTTPException(
            status_code=400,
            detail="agent-os-v2 native is in-process (no spawn; create a new session)",
        )
    client = await _ensure_client(harness_type, session_id)
    if client is None:
        raise HTTPException(status_code=404, detail="session not found")

    if harness_type == "claw":
        # claw multi-session = create another session on the same agent
        raise HTTPException(
            status_code=400,
            detail="claw multi-session: POST a new /h/claw/sessions (same agent_id)",
        )
    return await client.spawn_instance()


@router.delete("/{harness_type}/sessions/{session_id}")
async def delete_session(
    harness_type: str, session_id: str,
) -> Dict[str, Any]:
    _validate_type(harness_type)
    if harness_type == "agent-os-v2":
        rec = _sessions.pop(_key(harness_type, session_id), None)
        if rec is not None:
            try:
                await rec["emitter"].close()
            except Exception:
                pass
        ob_deleted = await _observe_delete_session(harness_type, session_id)
        _store.delete(harness_type, session_id)
        return {"session_id": session_id, "status": "deleted",
                "raw_deleted": True, "observe_deleted": ob_deleted}
    k = _key(harness_type, session_id)
    rec = _sessions.pop(k, None)
    if rec is None:
        # 内存无但 store 可能有(restore 失败的孤儿):清 store + observe,无 client 可 stop
        if _store.get(harness_type, session_id) is not None:
            _store.delete(harness_type, session_id)
            ob_deleted = await _observe_delete_session(harness_type, session_id)
            return {"session_id": session_id, "status": "deleted",
                    "raw_deleted": False, "observe_deleted": ob_deleted}
        raise HTTPException(status_code=404, detail="session not found")
    client = rec["client"]
    # raw delete first (jsonl transcript / gateway transcript), then stop.
    # client.delete() calls stop() itself on success; fall back to bare stop
    # if the client has no delete method or raw delete fails.
    raw_deleted = False
    delete_meth = getattr(client, "delete", None)
    if delete_meth is not None:
        try:
            if harness_type == "claude-code":
                r = await delete_meth(session_id)
            else:  # claw: delete takes no args
                r = await delete_meth()
            raw_deleted = bool(r.get("deleted"))
        except Exception as e:
            logger.warning("session raw delete error: %s", e)
            try:
                await client.stop()
            except Exception as e2:
                logger.warning("session stop fallback error: %s", e2)
    else:
        try:
            await client.stop()
        except Exception as e:
            logger.warning("session stop error: %s", e)
    # clear active if it pointed here
    if _active["type"] == harness_type and _active["id"] == session_id:
        _active.update(type="", id="")
    # sync the delete to observe (its SQLite is the TUI's session source of
    # truth). best-effort: failure here does NOT fail the orche delete.
    ob_deleted = await _observe_delete_session(harness_type, session_id)
    _store.delete(harness_type, session_id)
    return {"session_id": session_id, "status": "deleted",
            "raw_deleted": raw_deleted, "observe_deleted": ob_deleted}


# ── pickers (option lists for the frontend create/fork dialogs) ───────

@router.get("/claw/agents")
async def list_claw_agents() -> Dict[str, Any]:
    """List real claw agents for the picker.

    读 ~/.openclaw/openclaw.json 的 agents.list —— 真正的 agent 定义(含 model +
    后端),即 gateway agents.list RPC / sessions.send 认的 agent。

    不再读 channels.feishu.accounts:那是飞书 bot 凭证({appId, appSecret}),不是
    agent。旧实现把 feishu account keys 当 agent,致 default/origin-cc 等无 agent
    定义的飞书凭证混入 picker → flow sessions.send 时 session not found / timeout。
    agents.list 才是真 agent(main/claw-02/project-expert-00/english-expert/claw-03/
    claude,均 glm-5.2)。acp.allowedAgents 是 acpx 协议 agent(非 sessions.send),
    也不在此列。
    """
    import json as _json
    from pathlib import Path
    default_agents = ["main"]
    default_default = "main"
    try:
        cfg_path = Path.home() / ".openclaw" / "openclaw.json"
        with open(cfg_path) as f:
            cfg = _json.load(f) or {}
        agents_list = (cfg.get("agents") or {}).get("list") or []
        agents = [a.get("id") or a.get("name") for a in agents_list
                  if isinstance(a, dict) and (a.get("id") or a.get("name"))]
        if not agents:
            agents = default_agents
        default = "main" if "main" in agents else (agents[0] if agents else default_default)
        return {"agents": agents, "default": default}
    except Exception as e:
        logger.warning("list_claw_agents: read cfg failed: %s", e)
        return {"agents": default_agents, "default": default_default}


@router.get("/claude-code/cwds")
async def list_cc_cwds() -> Dict[str, Any]:
    """List deduped cwds of registered claude-code sessions for the picker.

    Scans _sessions for harness_type == 'claude-code', collects each client's
    .cwd (Path), dedupes. Returns {"cwds": [str,...], "default": "<first or DEFAULT_CWD>"}.
    """
    from .claude import DEFAULT_CWD
    seen: list[str] = []
    for v in _sessions.values():
        if v.get("harness_type") != "claude-code":
            continue
        client = v.get("client")
        cwd = getattr(client, "cwd", None)
        if cwd is None:
            continue
        s = str(cwd)
        if s not in seen:
            seen.append(s)
    if not seen:
        return {"cwds": [str(DEFAULT_CWD)], "default": str(DEFAULT_CWD)}
    return {"cwds": seen, "default": seen[0]}


# ── fork (branch a session's context into a new session) ──────────────

class ForkReq(BaseModel):
    source_session_id: str
    first_message: str
    new_session_id: Optional[str] = None  # cc only (new sid comes from stream)


@router.post("/{harness_type}/sessions/fork")
async def fork_session(
    harness_type: str, req: ForkReq,
) -> Dict[str, Any]:
    _validate_type(harness_type)
    source = req.source_session_id
    client = await _ensure_client(harness_type, source)
    if client is None:
        raise HTTPException(status_code=404, detail="source session not found")

    if harness_type == "claude-code":
        r = await client.fork(source, req.first_message)
        new_sid = req.new_session_id or r.get("new_sid")
        if not new_sid:
            raise HTTPException(
                status_code=500,
                detail=f"fork returned no new_sid: {r}",
            )
        # register the new session (new ClaudeClient on same cwd)
        cwd = str(getattr(client, "cwd", ""))
        # 守卫:new_sid 碰撞已存在 ext_id(罕见,caller 指定 new_session_id)→ 409,
        # 不静默覆盖活 client(旧 emitter WS / 子进程泄漏)
        if _key(harness_type, new_sid) in _sessions:
            raise HTTPException(status_code=409, detail="session already exists")
        new_client = await _create_claude(new_sid, cwd or None, native_sid=new_sid)
        _sessions[_key(harness_type, new_sid)] = {
            "client": new_client,
            "session_id": new_sid,
            "harness_type": harness_type,
            "agent_id": None,
            "native_sid": new_sid,   # fork 直接产原生 UUID → ext = native
            "cwd": cwd or None,
        }
        _store.create(new_sid, harness_type, native_sid=new_sid, cwd=cwd or None)
        return {"new_session_id": new_sid, "source": source,
                "forked": True, "detail": r}

    # claw: fork() is an ADR-4 stub (returns forked=False)
    r = await client.fork()
    if not r.get("forked"):
        raise HTTPException(
            status_code=501,
            detail={"forked": False, "error": r.get("error"),
                    "key": r.get("key"), "new_key": r.get("new_key")},
        )
    new_key = req.new_session_id or r.get("new_key")
    if not new_key:
        raise HTTPException(status_code=500, detail=f"fork returned no new_key: {r}")
    new_client = await _create_claw(new_key, None)
    _sessions[_key(harness_type, new_key)] = {
        "client": new_client,
        "session_id": new_key,
        "harness_type": harness_type,
        "agent_id": None,
        "native_sid": new_key,
        "cwd": None,
    }
    _store.create(new_key, harness_type, native_sid=new_key)
    return {"new_session_id": new_key, "source": source,
            "forked": True, "detail": r}


# ── startup restore (rebuild _sessions from _store after restart) ────

async def restore_all_sessions() -> Dict[str, int]:
    """启动重建:load _store → 按 harness_type 分派重建 client → 塞回 _sessions。

    claude:无状态重建(ClaudeClient(native_sid, cwd).connect() 只 observe WS,廉价;
    turn 时 spawn,client 本身无长连状态)。
    claw:有状态重连(OpenClawClient.start_background() 重连 gateway + v4 handshake +
    subscribe);gateway 不可达时 connect task 内部容错(running=False),不崩启动。
    ponytail:claw 重连失败后不自动重试(running=False → turn 时 503);后续可加
    healthcheck/自动重连。本次先"重启不丢 + 尝试重连"。
    """
    restored = {"claude-code": 0, "claw": 0, "agent-os-v2": 0, "failed": 0}
    for r in _store.list_all():
        ht = r["harness_type"]
        ext = r["ext_id"]
        key = _key(ht, ext)
        if key in _sessions:
            continue  # 已在内存(本轮 create 过)
        try:
            if ht == "claude-code":
                client = await _create_claude(ext, r.get("cwd"), native_sid=r.get("native_sid"))
                _sessions[key] = {
                    "client": client, "session_id": ext, "harness_type": ht,
                    "agent_id": None, "native_sid": r.get("native_sid"), "cwd": r.get("cwd"),
                }
                restored["claude-code"] += 1
            elif ht == "claw":
                client = await _create_claw(ext, r.get("agent_id"))
                _sessions[key] = {
                    "client": client, "session_id": ext, "harness_type": ht,
                    "agent_id": r.get("agent_id"),
                    "native_sid": r.get("native_sid") or ext, "cwd": None,
                }
                restored["claw"] += 1
            elif ht == "agent-os-v2":
                # native in-process agent 重建:从 store 回填 message_history(重启续聊)
                _sessions[key] = await _build_native_session(
                    ext, messages=_load_native_messages(ext)
                )
                restored["agent-os-v2"] += 1
        except Exception as e:
            logger.warning("restore session failed (%s/%s): %s", ht, ext, e)
            restored["failed"] += 1
    logger.info("harness session restore: %s", restored)
    return restored


# ── archive (summarize a session in place) ────────────────────────────
# ADR-3: drives an existing turn primitive with a summary prompt. The
# summary lands in the session transcript + observe, same as any turn.

class ArchiveReq(BaseModel):
    prompt: Optional[str] = None


@router.post("/{harness_type}/sessions/{session_id}/archive")
async def archive_session(
    harness_type: str, session_id: str, req: ArchiveReq,
) -> Dict[str, Any]:
    _validate_type(harness_type)
    client = await _ensure_client(harness_type, session_id)
    if client is None:
        raise HTTPException(status_code=404, detail="session not found")

    prompt = req.prompt or "用要点总结本 session 至今的事实、决策与未决项"

    if harness_type == "claw":
        if not getattr(client, "running", False):
            raise HTTPException(status_code=503, detail="claw client not connected yet")
        await client.send_message(prompt)
        return {"session_id": session_id, "status": "archived"}
    # claude-code: 同 trigger_turn 自动 resume(在原生 session 上下文里总结)
    resume = client.native_sid is not None
    result = await client.turn(prompt, resume=resume)
    _store.touch(session_id)
    return {"session_id": session_id, "status": "archived",
            "tick_id": result.get("tick_id")}


# ── flow engine (P2: turn chains / branches / DAG on trigger_turn) ────
# FlowDef JSON DSL → scheduler runs nodes over the existing turn primitive.
# 挂在 /h/flows(*):与 session primitive 同 router(/h prefix)。

from .flow import FlowDef, FlowScheduler, get_flow, _flows as _flow_registry

# flow_id → {def, state, scheduler, task}
# (_flow_registry in flow.py is the source of truth; this aliases for clarity)


@router.post("/flows")
async def create_flow(req: FlowDef) -> Dict[str, Any]:
    """Create a flow from a FlowDef. Validates the graph, returns flow_id."""
    req.validate_graph()
    flow_id = f"flow_{uuid.uuid4().hex[:12]}"
    scheduler = FlowScheduler(req, flow_id)
    _flow_registry[flow_id] = {
        "def": req, "state": scheduler.state, "scheduler": scheduler,
        "task": None,
    }
    return {"flow_id": flow_id, "status": "created",
            "nodes": [n.id for n in req.nodes],
            "edges": [{"from": e.from_, "to": e.to} for e in req.edges]}


@router.post("/flows/{flow_id}/run")
async def run_flow(flow_id: str) -> Dict[str, Any]:
    """Asynchronously execute a flow. Returns immediately; observe receives
    flow_started/node_started/node_completed/flow_completed events."""
    rec = get_flow(flow_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="flow not found")
    scheduler: FlowScheduler = rec["scheduler"]
    if rec.get("task") is not None and not rec["task"].done():
        raise HTTPException(status_code=409, detail="flow already running")
    task = scheduler.start_background()
    rec["task"] = task
    return {"flow_id": flow_id, "status": "running"}


@router.get("/flows/{flow_id}")
async def get_flow_status(flow_id: str) -> Dict[str, Any]:
    """Flow + per-node status."""
    rec = get_flow(flow_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="flow not found")
    return rec["state"].to_dict()


# ── switch (no prefix — mounted at app root as /switch) ───────────────
# Exposed via a separate include so it sits at POST /switch, not /h/switch.

switch_router = APIRouter(tags=["harness"])


@switch_router.post("/switch")
async def switch_session(req: SwitchReq) -> Dict[str, Any]:
    _validate_type(req.type)
    if _key(req.type, req.id) not in _sessions:
        raise HTTPException(status_code=404, detail="session not found")
    _active.update(type=req.type, id=req.id)
    return {"active": dict(_active), "status": "switched"}
