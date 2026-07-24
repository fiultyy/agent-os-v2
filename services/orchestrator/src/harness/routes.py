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
import copy
import uuid
from typing import Any, Dict, List, Optional

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

# F2(ADR-S4):native 异步 turn background tasks。持有引用防 GC(asyncio 不强引用
# create_task 产出 → 局部 task 出作用域被回收,coroutine 静默取消)。done 回调清引用
# 防无界增长。镜像 cc ClaudeClient._turn_tasks 形态,模块级因 native session 无 client
# 对象可附(task 闭包 rec/emitter,不属任何 client)。
_async_turn_tasks: Dict[str, asyncio.Task] = {}


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
    # F2(ADR-S4):native 异步 turn(fire-and-forget)。True → asyncio.create_task 包
    # agent.run,HTTP 立返 {status: started, tick_id};observe tick 事件流报进度。
    # 默认 False = 现有同步 {status: completed, response}(不破现有调用方/TUI/测试)。
    async_run: bool = False


class CancelTurnReq(BaseModel):
    # ADR-O6:协作式中断异步 turn。tick_id = trigger_turn(async_run=True) 返的 tick_id。
    tick_id: str


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


async def _emit_native_usage(emitter, session_id: str, usage) -> None:
    """usage + model → observe(TUI 状态栏 token/模型显示)。best-effort(ADR-7)。"""
    if emitter is None:
        return
    try:
        await emitter.emit({
            "event_type": "usage",
            "harness_type": "agent-os-v2",
            "harness_id": f"native_{session_id[:8]}",
            "session_id": session_id,
            "tick_id": "",
            "data": {
                "input": getattr(usage, "input_tokens", 0) or 0,
                "output": getattr(usage, "output_tokens", 0) or 0,
                "cache_read": getattr(usage, "cache_read_tokens", 0) or 0,
                "model": os.getenv("ANTHROPIC_MODEL", "glm-4.7"),
            },
        })
    except Exception:
        logger.warning("native usage emit failed (%s)", session_id)


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


def assemble_capabilities(
    spec,
    *,
    agent_id_for_scope: str,
    session_id: str,
    harness_id: str,
    emitter,
    session_key: str,
    cwd_scope: list,
):
    """Assemble the full peer capability stack for a native agent (ADR-4).

    Shared by ``_build_native_session`` (harness /h route) and
    ``a2a.transport.LocalTransport`` (internal mesh message/send). Both build a
    peer agent with the FULL capability set — A2A consumed agents are peers,
    not stripped subagents.

    ``agent_id_for_scope`` is the agent_id the MemoryWriterCapability scopes
    memory writes to. Per ADR-4 this MUST be the *consumed* agent's id, never
    the caller's — the single shared chokepoint that enforces scope isolation.
    For the /h session path it is the session_id (session-scoped memory); for
    LocalTransport it is the target agent_id (the consumed agent owns its own
    memory).

    Returns ``(capabilities, model_name)``. Caller builds resources (emitter,
    cwd_scope, session_key) then calls this, then calls ``build_native_agent``.

    DRY:single assembly site — no pasted capability blocks. Adding a capability
    here updates both the harness session and the A2A consumed agent.
    """
    from .capabilities import (
        GuardrailCapability,
        MemoryCapability,
        MemoryWriterCapability,
        MultiCwdScopeCapability,
        ObserveCapability,
        ToolBridgeCapability,
        make_profile_capabilities,
        make_skill_capabilities,
    )
    from src.services import _state
    from src.skills.skill_loader import SkillLoader
    from src.tools.guardrail import Guardrail
    from src.agent.agent_spec import normalize_agent_id

    spec_id = normalize_agent_id(spec.id)
    try:
        all_skills = make_skill_capabilities(SkillLoader())
    except Exception:
        all_skills = []
    if spec.skills:
        wanted = set(spec.skills)
        skill_caps = [c for c in all_skills if c.id in wanted]
    else:
        skill_caps = all_skills
    caps = [
        ObserveCapability(emitter=emitter, harness_id=harness_id, session_id=session_id,
                          agent_id=spec_id),
        MemoryWriterCapability(
            memory_event_bus=_state.memory_event_bus,
            knowledge_graph=_state.knowledge_graph,
            agent_id=agent_id_for_scope,
            session_id=session_id,
        ),
        ToolBridgeCapability(
            tool_executor=_state.tool_executor,
            pitfail_registry=_state.pitfail_registry,
            tool_allow=spec.tools.allow or None,
            tool_deny=spec.tools.deny or None,
        ),
        GuardrailCapability(guardrail=Guardrail()),
        *skill_caps,
    ]
    mt = _get_memory_tools()
    if mt is not None:
        caps.append(MemoryCapability(experience_tool=mt[0], kg_tool=mt[1]))
    caps.extend(make_profile_capabilities(
        _state.profile_registry.get(spec_id) if _state.profile_registry else None
    ))
    caps.append(MultiCwdScopeCapability(cwd_scope=cwd_scope, session_key=session_key))
    return caps, spec.model or None


async def _build_native_session(
    session_id: str, messages: list | None = None,
    agent_id: str | None = None,
) -> Dict[str, Any]:
    """agent-os-v2 native in-process session:pydantic-ai Agent + ObserveEmitter + 消息历史。

    ADR pydantic-ai-v2-adoption P8:native turn 走 /h/agent-os-v2。capabilities 注入
    ObserveCapability(真 emitter→observe)+ GuardrailCapability(护 native tool)。
    memory(P3 recall,defer_loading)+ skill 已通电。

    Capability assembly is shared with ``a2a.transport.LocalTransport`` via
    ``assemble_capabilities`` (DRY, ADR-4 peer stack). This function owns
    session-scoped resource construction (spec resolve, emitter connect,
    cwd_scope, default_cwd init) + cache_control model_settings; the
    capability list + model_name come from the shared assembler.
    """
    from .native_agent import HARNESS_TYPE, build_native_agent
    from .emit import ObserveEmitter
    from src.services import _state
    from src.agent.agent_spec import normalize_agent_id

    registry = _state.agent_registry
    if registry is not None and agent_id:
        spec = registry.get(agent_id) or registry.default()
    elif registry is not None:
        spec = registry.default()
    else:
        from src.agent.agent_spec import AgentSpec
        spec = AgentSpec(id="native", default=True, cwds=[])
    spec_id = normalize_agent_id(spec.id)
    session_key = _key("agent-os-v2", session_id)
    repo_root = os.getenv("AO2_REPO_ROOT", os.getcwd())
    if registry is not None:
        cwd_scope = registry.resolve_cwd_scope(spec, repo_root=repo_root)
        default_cwd = next(
            (e.path_abs for e in cwd_scope if e.default), cwd_scope[0].path_abs
        )
    else:
        ws_abs = os.path.join(
            os.path.expanduser(os.getenv("AO2_STATE_DIR", "~/.agent-os")),
            "agents", spec_id, "workspace")
        from src.agent.agent_registry import ResolvedCwd
        cwd_scope = [ResolvedCwd(path_abs=ws_abs, label=spec_id, default=True)]
        default_cwd = ws_abs

    harness_id = f"native_{session_id[:8]}"
    emitter = ObserveEmitter(HARNESS_TYPE, harness_id=harness_id, session_id=session_id)
    try:
        await emitter.connect()
    except Exception:
        logger.warning("native emitter connect failed (%s)", harness_id)

    caps, model_name = assemble_capabilities(
        spec,
        agent_id_for_scope=session_id,
        session_id=session_id,
        harness_id=harness_id,
        emitter=emitter,
        session_key=session_key,
        cwd_scope=cwd_scope,
    )
    from .mcp_config import load_global_mcp_servers
    global_mcp = load_global_mcp_servers()
    agent = build_native_agent(
        # ADR-1:接 AgentSpec.instructions(79e9e92 引入的 dead 字段)。空串兜底
        # 维持 default 旧行为;非空透传进 Agent(instructions=...) base 段。cache
        # 耦合见 native_agent.build_native_agent docstring(instructions 必须静态)。
        instructions=spec.instructions or "",
        capabilities=caps,
        model_settings={
            "anthropic_cache_instructions": "5m",
            "anthropic_cache_tool_definitions": "5m",
        },
        mcp_servers=global_mcp or None,
        model_name=model_name,
    )
    from src.tools.cwd_scope import set_session_active_cwd
    set_session_active_cwd(session_key, default_cwd)
    return {
        "agent": agent, "emitter": emitter, "messages": messages or [],
        "session_id": session_id, "harness_type": "agent-os-v2",
        "native_sid": session_id,
        "cwd_scope": cwd_scope, "spec_id": spec_id,
    }


async def _run_native_turn_async(
    rec: Dict[str, Any], session_id: str, message: str, tick_id: str,
) -> None:
    """F2:background runner for async native turns.

    复用同步路径的全部副作用(ObserveCapability tick lifecycle / messages 持久化 /
    usage emit),仅异步化。异常仅 log(已 started 的 HTTP 响应无法回传错误;observe
    tick_completed(status=error) 由 ObserveCapability 异常分支自动闭环)。

    tick_id(orchestration 生成)经 metadata={"tick_id":...} 注入 agent.run →
    ObserveCapability 读 ctx.metadata,使 emit 的 tick_started/tick_completed 用
    同一个 tick_id(单域);cancel 端点 emit 的 tick_completed(cancelled) 同 tick_id。
    """
    try:
        result = await rec["agent"].run(
            message, message_history=rec["messages"], metadata={"tick_id": tick_id},
        )
        rec["messages"] = result.all_messages()
        _store.touch(session_id)
        _persist_native_messages(session_id, rec["messages"])
        await _emit_native_usage(rec.get("emitter"), session_id, result.usage)
    except Exception:
        logger.exception("async native turn failed (session=%s)", session_id)


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
        # §8.1:传 agent_id → _build_native_session 取对应 spec(per-agent profile/skills/cwd)。
        rec = await _build_native_session(session_id, agent_id=req.agent_id)
        _sessions[key] = rec
        # §D6:spec.id 落库 agent_id 列(restore/续聊按此重建同 spec session)。
        _store.create(
            session_id, "agent-os-v2",
            native_sid=session_id, agent_id=rec["spec_id"],
        )
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
            row = _store.get(harness_type, session_id)
            if row is None:
                raise HTTPException(status_code=404, detail="session not found")
            # §8.5:从 store 取 agent_id 重建对应 spec 的 session(续聊保留 per-agent 配置)
            rec = await _build_native_session(
                session_id, messages=_load_native_messages(session_id),
                agent_id=row.get("agent_id"),
            )
            _sessions[_key(harness_type, session_id)] = rec
        # §7.3:turn 前从 session 持久恢复 _active_cwd(跨 run/跨 turn 保活激活 cwd)。
        from pathlib import Path
        from src.tools.cwd_scope import _active_cwd, get_session_active_cwd
        session_key = _key(harness_type, session_id)
        stored = get_session_active_cwd(session_key)
        if stored:
            _active_cwd.set(Path(stored))
        else:
            # fallback:spec 的 default cwd(rec['cwd_scope'] 首个 default 项)
            scope = rec.get("cwd_scope") or []
            default_cwd = next((e.path_abs for e in scope if e.default),
                               scope[0].path_abs if scope else None)
            if default_cwd:
                _active_cwd.set(Path(default_cwd))
        # in-process Agent run:ObserveCapability 自动推 observe,guardrail 自动护
        if req.async_run:
            # F2(ADR-S4):异步 fire-and-forget。create_task 包整 turn → HTTP 立返
            # started;observe tick_started→tick_completed 事件流报进度(ObserveCapability
            # 自动 emit,与同步路径同生命周期)。镜像 cc ClaudeClient.turn。
            tick_id = str(uuid.uuid4())
            task = asyncio.create_task(
                _run_native_turn_async(rec, session_id, req.message, tick_id)
            )
            _async_turn_tasks[tick_id] = task
            task.add_done_callback(lambda t, k=tick_id: _async_turn_tasks.pop(k, None))
            return {"session_id": session_id, "status": "started", "tick_id": tick_id}
        result = await rec["agent"].run(req.message, message_history=rec["messages"])
        rec["messages"] = result.all_messages()
        _store.touch(session_id)
        _persist_native_messages(session_id, rec["messages"])  # 续聊持久化(重启不丢)
        await _emit_native_usage(rec.get("emitter"), session_id, result.usage)
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


@router.post("/{harness_type}/sessions/{session_id}/turn/cancel")
async def cancel_turn(
    harness_type: str, session_id: str, req: CancelTurnReq,
) -> Dict[str, Any]:
    """ADR-O6:协作式中断异步 turn(止损跑飞的 _async_turn_tasks)。

    tick_id = trigger_turn(async_run=True) 返回值。cancel() 后:
    1) task.cancel()(asyncio 协作式;能否干净打断 GLM httpx 请求不定,见 ADR-O6 scope limit)
    2) emit tick_completed(status=cancelled) 经 observe WS(TUI 树节点刷新为取消态)
    3) 从 _async_turn_tasks 移除(显式 pop;add_done_callback 的 pop 是幂等兜底)

    tick_id 不在 registry → 404 JSON(已结束/从未存在)。
    不碰 workflow_engine.py(R1);observe 侧只经 emitter.emit,不引 memory(R5)。
    """
    _validate_type(harness_type)
    # pop-or-get 原子化:消 get 与后续 pop 间残留窗口竞态(两并发 cancel 同 tick_id
    # 都过 get 检查,双 cancel)。pop 命中即独占;done_callback 的 pop 是幂等兜底。
    task = _async_turn_tasks.pop(req.tick_id, None)
    if task is None:
        raise HTTPException(
            status_code=404,
            detail=f"tick_id {req.tick_id} not found (already done or never started)",
        )
    task.cancel()
    # emit tick_completed(cancelled) — 复用 native session 的 emitter(fire-and-forget)。
    # ponytail:emitter 从 _sessions rec 取;无 rec(None/已删)则跳过 emit,task 已取消仍是真。
    rec = _sessions.get(_key(harness_type, session_id))
    emitter = rec.get("emitter") if rec else None
    if emitter is not None:
        from .events import tick_completed as _tick_completed
        harness_id = (rec.get("harness_id") if rec else None) or f"native_{session_id[:8]}"
        try:
            await emitter.emit(_tick_completed(
                harness_type, harness_id, session_id, req.tick_id,
                status="cancelled", response="cancelled by user",
            ))
        except Exception:
            logger.warning("cancel tick_completed emit failed (%s/%s)", session_id, req.tick_id)
    return {"session_id": session_id, "tick_id": req.tick_id, "status": "cancelled"}


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


@router.get("/agent-os-v2/agents")
async def list_ao2_agents() -> Dict[str, Any]:
    """List agent-os-v2 registry agents for the TUI new-session picker (ADR-3).

    Returns ``{agents: [{id, name, default}]}`` projected from
    ``_state.agent_registry._agents`` (id/name/default fields). ``default`` is
    True only for ``_default_id``. registry None (engine not booted) →
    ``{agents: []}`` so the TUI degrades gracefully without crashing.

    Contract is fixed (worker-C T1/T2 parallel): front-end parses this exact
    shape. name None → fall back to id (picker always shows something).
    """
    from src.services import _state
    registry = _state.agent_registry
    if registry is None:
        return {"agents": []}
    default_id = registry._default_id
    out: list[Dict[str, Any]] = []
    for aid, spec in registry._agents.items():
        out.append({
            "id": aid,
            "name": spec.name or aid,
            "default": aid == default_id,
        })
    return {"agents": out}


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

class ForkTarget(BaseModel):
    """F1:fan-out 单分支声明。first_message = 该分支的探路方向/种子消息。"""
    first_message: str


class ForkReq(BaseModel):
    source_session_id: str
    first_message: str
    new_session_id: Optional[str] = None  # cc only (new sid comes from stream)
    # F1(ADR-S3):native fan-out。空 → 回退单 first_message(向后兼容 cc/claw)。
    targets: List[ForkTarget] = []


@router.post("/{harness_type}/sessions/fork")
async def fork_session(
    harness_type: str, req: ForkReq,
) -> Dict[str, Any]:
    _validate_type(harness_type)
    source = req.source_session_id

    # F1(ADR-S2):native fork = 深拷贝源 ModelMessages + 继承源 agent_id + parent lineage。
    # 必须在 _ensure_client 之前:native rec 无 "client" 键(_ensure_client 取 rec["client"]),
    # 走 cc/claw 路径恒 KeyError/404。1→N fan-out 只此分支(ADR-S3:cc 保持 1:1 不动)。
    if harness_type == "agent-os-v2":
        rec = _sessions.get(_key(harness_type, source))
        if rec is None:
            # store 有、内存无(restore 孤儿)→ 重建一次取 messages + agent_id
            row = _store.get(harness_type, source)
            if row is None:
                raise HTTPException(status_code=404, detail="source session not found")
            rec = await _build_native_session(
                source, messages=_load_native_messages(source),
                agent_id=row.get("agent_id"),
            )
            _sessions[_key(harness_type, source)] = rec
        src_agent_id = rec.get("spec_id")
        src_messages = rec.get("messages") or []

        # fan-out targets:空 → 单 first_message(向后兼容);非空 → 每 target 一 fork(同父克隆)。
        targets = req.targets or [ForkTarget(first_message=req.first_message)]
        forks: List[Dict[str, Any]] = []
        for tgt in targets:
            new_sid = str(uuid.uuid4().hex[:12])
            # 深拷贝:新 list + 拷 message 对象(非浅引用;改新 session 不影响源)。
            # pydantic-ai ModelMessage 为 pydantic 模型,deepcopy 安全。
            cloned = copy.deepcopy(src_messages)
            new_rec = await _build_native_session(
                new_sid, messages=cloned, agent_id=src_agent_id,
            )
            _sessions[_key(harness_type, new_sid)] = new_rec
            _store.create(
                new_sid, "agent-os-v2",
                native_sid=new_sid, agent_id=src_agent_id,
                parent_session_id=source,
            )
            # F3(ADR-S5):emit branch_created → observe 回填 child parent_session_id。
            # child=新 session_id;session_id 字段载 child(branch_created 的语义:
            # 此事件属于新 fork 分支)。agent_id 透传(D),让 observe 知道谁的 fork。
            # fire-and-forget(ADR-7):emit 失败不阻塞 fork。
            fork_emitter = new_rec.get("emitter")
            if fork_emitter is not None:
                from .events import branch_created as _branch_created
                try:
                    await fork_emitter.emit(_branch_created(
                        "agent-os-v2", new_rec.get("harness_id", ""),
                        new_sid, branch_id=new_sid, parent_branch_id=source,
                        agent_id=src_agent_id or "",
                    ))
                except Exception:
                    logger.warning("branch_created emit failed for %s", new_sid)
            forks.append({
                "new_session_id": new_sid, "source": source,
                "direction": tgt.first_message, "forked": True,
            })
        return {"forks": forks, "source": source, "forked": True}

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
                # native in-process agent 重建:从 store 取 agent_id 重建对应 spec +
                # 回填 message_history(重启续聊保留 per-agent 配置,§8.5)
                _sessions[key] = await _build_native_session(
                    ext, messages=_load_native_messages(ext),
                    agent_id=r.get("agent_id"),
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
