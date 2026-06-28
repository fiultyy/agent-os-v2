"""Runtime observer hook — introspective observability (error-spike detection).

A non-invasive OBSERVER-priority hook that reads the in-memory
``_state.execution_log`` (per-step ``status``, already recorded by chat.py's
``log_execution_step``) to detect error spikes per session/agent and surface
them as runtime observations.

Design choices (memory red-line safe):
- **chat.py is untouched** — ``execution_log`` already records per-step
  ``done``/``error`` status, so the hook derives turn outcome from it rather
  than requiring chat.py to call a new ``record_turn_outcome`` (avoids any edit
  to the chat.py main path).
- **Pure read of ``_state``** — never touches ``memory_service`` / the recall
  path / scoring weights (R1-R8). Writes only to ``_state.runtime_observations``
  (in-memory ring buffer).
- **Returns None** — OBSERVER contract; does not override DefaultMemoryHook
  results (``emit`` takes last non-None).
- **Non-fatal** — ``event_bus.emit`` already isolates hook exceptions
  (event_bus.py:138-144); under ``MEMORY_EVENT_BUS_ENABLED=0`` degradation,
  OBSERVER hooks are skipped entirely.

Naming: deliberately ``RuntimeObserver`` / ``error_spike`` — ``AnomalyType`` is
taken by ``neural_field.py`` and ``ObservabilityEventType`` by the canvas
front-end, so those names are avoided.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from src.memory.hooks import HookPriority, MemoryHook, SessionContext, TurnContext
from src.services import _state

# Spike window + threshold (step-level): flag an error_spike when >= this many
# errors appear among the last ``_SPIKE_WINDOW`` execution-log steps for a
# session. Values mirror the migrated conversation-observer rule (>3 errors).
_SPIKE_WINDOW = 8
_SPIKE_THRESHOLD = 3
# Dedup: don't re-flag the same (agent, session) within this many seconds, so a
# sustained spike doesn't flood the buffer on every turn.
_DEDUP_SECONDS = 60.0


class RuntimeObserverHook(MemoryHook):
    """Detects execution error spikes and surfaces them as runtime observations.

    Priority is OBSERVER (runs after the SYSTEM core). Reads only
    ``_state.execution_log`` + ``_state.runtime_observations`` — zero writes to
    memory_service, zero LLM, zero failure surface beyond a non-fatal log.
    """

    priority = HookPriority.OBSERVER

    def __init__(self) -> None:
        # (agent_id, session_id) -> ISO timestamp of the last flagged spike.
        self._last_spike: dict[tuple[str, str], str] = {}

    async def on_turn_end(self, ctx: TurnContext) -> None:
        self._maybe_flag_spike(ctx.agent_id, ctx.session_id)

    async def on_session_end(self, ctx: SessionContext) -> None:
        self._maybe_flag_spike(ctx.agent_id, ctx.session_id)

    def _maybe_flag_spike(self, agent_id: str, session_id: str) -> None:
        recent = _recent_steps_for_session(session_id, _SPIKE_WINDOW)
        # Need at least the threshold of steps observed before flagging, so a
        # fresh session with one error doesn't cry wolf.
        if len(recent) < _SPIKE_THRESHOLD:
            return
        error_count = sum(1 for s in recent if s.get("status") == "error")
        if error_count < _SPIKE_THRESHOLD:
            return
        # Dedup within the window.
        key = (agent_id, session_id)
        last = self._last_spike.get(key)
        if last is not None and _seconds_since(last) < _DEDUP_SECONDS:
            return
        self._last_spike[key] = _now_iso()
        _state.record_observation(
            "error_spike",
            agent_id,
            {
                "session_id": session_id,
                "errors": error_count,
                "window": _SPIKE_WINDOW,
            },
        )


# ── Helpers ────────────────────────────────────────────────────────


def _recent_steps_for_session(session_id: str, window: int) -> list[dict[str, Any]]:
    """Return up to ``window`` most-recent execution-log steps for a session."""
    steps = [s for s in _state.execution_log if s.get("session_id") == session_id]
    return steps[-window:]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _seconds_since(iso_ts: str) -> float:
    """Seconds elapsed since ``iso_ts``; on parse failure treat as stale."""
    try:
        then = datetime.fromisoformat(iso_ts)
    except ValueError:
        return _DEDUP_SECONDS
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - then).total_seconds()
