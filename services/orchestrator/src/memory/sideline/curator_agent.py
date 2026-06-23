"""CuratorAgent — offline LLM quality-assurance pass [④].

Background (offline-batch) curator: scans ``origin=AGENT`` memories in
STALE/ACTIVE states and asks an LLM to produce a curation plan —
archive (redundant/obsolete), merge (cluster → consolidated SEMANTIC),
correct (fix factual errors in content). The plan is applied back to the
store.

Contract:
- **Never inserted into the synchronous ``run_maintenance`` chain** — that
  chain is a Zero-LLM contract (prune / forget / migrate only). The
  integration phase fires ``curate`` as an independent
  ``asyncio.create_task`` after the db-watcher lock is released
  (fire-and-forget).
- **Relaxed timeout** (20s default): offline batch, not on the request
  hot-path.
- **Never raises**: LLM timeout / parse failure / application failure
  degrades to the deterministic chain
  (``state_pruner.prune`` + ``active_forgetting.run_sweep``).

The degrade path is intentionally a superset of the Zero-LLM maintenance
chain — it guarantees forward progress even when the LLM is unavailable,
and never re-touches FOREGROUND (user-authored) memories (both prune and
run_sweep filter ``origin=AGENT`` only — P0 provenance).
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any

from src.memory.hooks import CurateContext, MemoryHook, HookPriority
from src.memory.service import MemoryService
from src.memory.types import (
    MemoryFilter,
    MemoryOrigin,
    MemoryScope,
    MemoryState,
    MemoryType,
)

logger = logging.getLogger(__name__)


_CURATE_PROMPT = (
    "你是记忆策展助手。下面是同一 agent 的一批 origin=AGENT 自沉淀记忆(STALE/ACTIVE)。\n"
    "请审查它们的质量并输出一个 JSON 策展计划。可选三类操作:\n"
    "  - archive: 冗余/过时/低价值,直接归档(id 列表)\n"
    "  - merge: 一组高度重叠的记忆合并成一条新的 SEMANTIC 总结\n"
    '        [{{"source_ids":[...], "summary":"...", "importance":0.0-1.0}}]\n'
    "  - correct: 记忆内容有事实错误,给出修正\n"
    '        [{{"id":"...", "content_fix":"..."}}]\n\n'
    "严格只输出 JSON,不要任何额外文字或代码块标记。格式:\n"
    '{{"archive":[...], "merge":[{{"source_ids":[...], "summary":"...", '
    '"importance":0.5}}], "correct":[{{"id":"...", "content_fix":"..."}}]}}\n\n'
    "候选记忆(JSONL, id | state | importance | content):\n{candidates}"
)


@dataclass
class CurateResult:
    """Outcome of a CuratorAgent.curate call (SideAgentResult)."""

    triggered: bool = False
    scanned: int = 0
    archived: list[str] = field(default_factory=list)
    merged: list[str] = field(default_factory=list)  # new SEMANTIC ids
    corrected: list[str] = field(default_factory=list)
    degraded: bool = False
    error: str = ""


class CuratorAgent:
    """Offline LLM quality-assurance curator (archive / merge / correct)."""

    def __init__(
        self,
        memory_service: MemoryService,
        llm_client: Any | None = None,
    ) -> None:
        self._memory = memory_service
        self._llm = llm_client

    async def curate(
        self,
        agent_id: str,
        scope: str = "all",
        timeout: float | None = None,  # None → _state.SIDELLM_TIMEOUT
    ) -> CurateResult:
        """Run an offline curation pass for ``agent_id``.

        Args:
            agent_id: Agent whose ``origin=AGENT`` STALE/ACTIVE memories to
                curate.
            scope: ``all`` / ``episodic`` / ``semantic`` (informational;
                forwarded to the candidate filter as a memory-type hint).
            timeout: LLM call timeout (relaxed — offline batch).

        Returns:
            :class:`CurateResult`. Never raises — LLM failure degrades to
            the deterministic prune + forget chain.
        """
        result = CurateResult(triggered=True)
        from src.services import _state
        if timeout is None:
            timeout = _state.SIDELLM_TIMEOUT
        # (agent_id is the call argument, not part of the CurateResult
        #  payload — keep the outcome lean.)

        candidates = await self._collect_candidates(agent_id, scope)
        result.scanned = len(candidates)
        if not candidates:
            return result

        try:
            plan = await asyncio.wait_for(
                self._ask_llm(candidates), timeout=timeout
            )
        except asyncio.TimeoutError:
            logger.warning(
                "CuratorAgent LLM curate timed out (%.1fs) — degrading", timeout
            )
            return await self._degrade(agent_id)
        except Exception as exc:
            logger.warning("CuratorAgent LLM curate failed: %s — degrading", exc)
            return await self._degrade(agent_id)

        try:
            await self._apply(plan, result, agent_id)
        except Exception as exc:
            # Apply is best-effort; partial application is acceptable.
            logger.warning("CuratorAgent apply failed: %s", exc)
            result.error = str(exc)
        return result

    # ── candidate collection ────────────────────────────────────────

    async def _collect_candidates(
        self, agent_id: str, scope: str
    ) -> list[dict[str, Any]]:
        """Return ``origin=AGENT`` STALE+ACTIVE memories as plan input.

        P0 provenance: only agent-self-sedimented memories are eligible;
        FOREGROUND (user / external-app) memories are never curated.
        We fetch STALE and ACTIVE separately because ``MemoryFilter.state``
        is a single-value filter.
        """
        memory_type = None
        if scope == "episodic":
            memory_type = MemoryType.EPISODIC
        elif scope == "semantic":
            memory_type = MemoryType.SEMANTIC

        out: list[dict[str, Any]] = []
        for state in (MemoryState.STALE, MemoryState.ACTIVE):
            f = MemoryFilter(
                agent_id=agent_id,
                origin=MemoryOrigin.AGENT,
                state=state,
                memory_type=memory_type,
            )
            try:
                items = await self._memory._store.search(f)
            except Exception as exc:
                logger.warning("CuratorAgent candidate search failed: %s", exc)
                continue
            for it in items:
                out.append(
                    {
                        "id": it.id,
                        "state": it.state.value if it.state else "active",
                        "importance": float(it.importance),
                        "content": it.content,
                    }
                )
        return out

    # ── LLM ask ─────────────────────────────────────────────────────

    async def _ask_llm(self, candidates: list[dict[str, Any]]) -> dict[str, Any]:
        """Ask the LLM for a curation plan. Raises on no-LLM / bad JSON."""
        if self._llm is None:
            raise RuntimeError("no LLM client configured")

        lines = []
        for c in candidates:
            lines.append(
                f'{c["id"]} | {c["state"]} | {c["importance"]:.2f} | {c["content"]}'
            )
        candidates_text = "\n".join(lines)
        prompt = _CURATE_PROMPT.format(candidates=candidates_text[:6000])

        response = await self._llm.chat(
            [{"role": "user", "content": prompt}],
            max_tokens=800,
            temperature=0.2,
        )
        return self._parse(response or "")

    @staticmethod
    def _parse(text: str) -> dict[str, Any]:
        """Parse the LLM's JSON curation plan (tolerant of code fences)."""
        body = text.strip()
        # Strip ```json ... ``` fences if present.
        if body.startswith("```"):
            body = body.strip("`")
            # Drop a leading language tag like 'json\n'.
            if body.lower().startswith("json"):
                body = body[4:].lstrip()
        # Find the outermost JSON object.
        start = body.find("{")
        end = body.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ValueError("no JSON object in LLM response")
        plan = json.loads(body[start : end + 1])
        # Normalize keys to the three supported actions.
        normalized: dict[str, Any] = {"archive": [], "merge": [], "correct": []}
        for key in ("archive", "merge", "correct"):
            val = plan.get(key)
            if isinstance(val, list):
                normalized[key] = val
        return normalized

    # ── apply plan ──────────────────────────────────────────────────

    async def _apply(
        self,
        plan: dict[str, Any],
        result: CurateResult,
        agent_id: str,
    ) -> None:
        """Apply the curation plan to the store (best-effort, per-item)."""

        # (1) archive
        for mid in plan.get("archive", []):
            if not isinstance(mid, str):
                continue
            try:
                updated = await self._memory.update(
                    memory_id=mid, archived=True, state=MemoryState.ARCHIVED
                )
                if updated is not None:
                    result.archived.append(mid)
            except Exception as exc:
                logger.warning("CuratorAgent archive %s failed: %s", mid, exc)

        # (2) merge — write a new SEMANTIC (origin=AGENT), archive sources
        for entry in plan.get("merge", []):
            if not isinstance(entry, dict):
                continue
            source_ids = entry.get("source_ids") or []
            summary = entry.get("summary")
            if not summary or not isinstance(summary, str):
                continue
            importance = entry.get("importance", 0.5)
            try:
                importance = max(0.0, min(1.0, float(importance)))
            except (TypeError, ValueError):
                importance = 0.5
            try:
                ref = await self._memory.store(
                    content=summary,
                    agent_id=agent_id,
                    memory_type=MemoryType.SEMANTIC,
                    scope=MemoryScope.AGENT,
                    importance=importance,
                    origin=MemoryOrigin.AGENT,
                    metadata={
                        "source": "curator_agent",
                        "merged_from": [s for s in source_ids if isinstance(s, str)],
                    },
                )
                result.merged.append(ref.id)
                # Archive the source memories (consolidation replaces them).
                for sid in source_ids:
                    if not isinstance(sid, str):
                        continue
                    try:
                        await self._memory.update(
                            memory_id=sid,
                            archived=True,
                            state=MemoryState.ARCHIVED,
                        )
                    except Exception as exc:
                        logger.warning(
                            "CuratorAgent merge-archive %s failed: %s", sid, exc
                        )
            except Exception as exc:
                logger.warning("CuratorAgent merge store failed: %s", exc)

        # (3) correct — fix content
        for entry in plan.get("correct", []):
            if not isinstance(entry, dict):
                continue
            mid = entry.get("id")
            fix = entry.get("content_fix")
            if not isinstance(mid, str) or not isinstance(fix, str):
                continue
            try:
                updated = await self._memory.update(memory_id=mid, content=fix)
                if updated is not None:
                    result.corrected.append(mid)
            except Exception as exc:
                logger.warning("CuratorAgent correct %s failed: %s", mid, exc)

    # ── degrade (deterministic fallback) ────────────────────────────

    async def _degrade(self, agent_id: str) -> CurateResult:
        """Deterministic fallback when the LLM is unavailable.

        Chains the Zero-LLM maintenance primitives:
        ``state_pruner.prune`` → ``active_forgetting.run_sweep``. Both filter
        ``origin=AGENT`` only (P0 provenance) so FOREGROUND memories are
        never touched.
        """
        from src.services import _state
        _state.record_degrade("curator")
        result = CurateResult(triggered=True, degraded=True)
        try:
            # Imported here (not at module top) to avoid a circular import
            # and to read live state wired by the engine at runtime.
            from src.services import _state

            if _state.state_pruner is not None:
                try:
                    pr = await _state.state_pruner.prune(agent_id=agent_id)
                    result.archived.extend(getattr(pr, "archived_ids", []) or [])
                except Exception as exc:
                    logger.warning("CuratorAgent degrade prune failed: %s", exc)

            if _state.active_forgetting is not None:
                try:
                    fr = await _state.active_forgetting.run_sweep(agent_id=agent_id)
                    result.archived.extend(getattr(fr, "archived_ids", []) or [])
                except Exception as exc:
                    logger.warning("CuratorAgent degrade forget failed: %s", exc)
        except Exception as exc:
            # _state not wired yet (e.g. unit test without engine) — degrade
            # is a no-op but still returns cleanly.
            logger.warning("CuratorAgent degrade skipped (_state unavailable): %s", exc)
            result.error = str(exc)
        return result


class CuratorHook(MemoryHook):
    """Lifecycle hook that triggers CuratorAgent on ``EventType.CURATE``.

    OBSERVER priority (does not gate the core Zero-LLM maintenance chain).
    The integration layer is responsible for firing ``curate`` as an
    independent ``asyncio.create_task`` — this hook only provides the
    entry point.
    """

    priority = HookPriority.OBSERVER

    def __init__(self, curator: CuratorAgent) -> None:
        self._curator = curator

    async def on_curate(self, ctx: CurateContext) -> None:
        try:
            await self._curator.curate(
                agent_id=ctx.agent_id, scope=getattr(ctx, "scope", "all")
            )
        except Exception as exc:
            # CuratorAgent.curate never raises, but guard the hook boundary.
            logger.warning("CuratorHook on_curate failed: %s", exc)
