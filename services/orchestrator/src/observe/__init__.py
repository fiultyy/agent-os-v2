"""Observe client for orchestrator.

Fire-and-forget WebSocket client to observe-service (port 8002).
Zero-regression red-line: observe-service down → silent logger.warning, never raise.
"""

from .client import ObserveClient

__all__ = ["ObserveClient"]
