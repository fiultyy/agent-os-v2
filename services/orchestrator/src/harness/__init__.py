"""Harness layer — orchestrator is the ONLY harness client (ADR-4).

Thin primitive API over {claw, claude-code}:
- routes: 6 primitive endpoints (sessions CRUD + turn + spawn + switch)
- openclaw: v4 handshake + sessions.send + subscribe + event mapping +
  per-runId tick_started synthesis + token_delta
- claude: PTY spawn (claude --resume / -p stream-json) + stream-json parser
- emit: WS client to observe /ws/ingest
- events: ObserveEvent dict constructors (self-contained, mirrors observe schema)

Event flow: harness → orchestrator (connect + map) → observe /ws/ingest → TUI.
"""

from .routes import router, switch_router

__all__ = ["router", "switch_router"]
