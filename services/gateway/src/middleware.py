"""Auth middleware and rate-limiting for the gateway.

Provides a FastAPI dependency that validates JWT bearer tokens on protected
routes, and an in-memory sliding-window rate limiter for auth endpoints.
"""

from __future__ import annotations

import time
from collections import defaultdict
from typing import Any

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from src.auth import TokenValidationError, decode_token
from src.config import AUTH_ENABLED

# ── Auth dependency ───────────────────────────────────────────────────────────

_bearer_scheme = HTTPBearer(auto_error=False)

# Paths that never require authentication.
PUBLIC_PATHS: frozenset[str] = frozenset({
    "/health",
    "/docs",
    "/openapi.json",
    "/redoc",
})


async def require_auth(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> dict[str, Any]:
    """FastAPI dependency that enforces JWT authentication.

    When ``AUTH_ENABLED`` is ``False`` the dependency is a no-op, preserving
    backward compatibility with unauthenticated deployments.

    Returns:
        Token claims dict when auth is enabled, empty dict otherwise.
    """
    if not AUTH_ENABLED:
        return {}

    # Allow public paths without auth.
    path = request.url.path.rstrip("/")
    if path in PUBLIC_PATHS or path.startswith("/auth"):
        return {}

    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header",
        )

    try:
        claims = decode_token(credentials.credentials, expected_type="access")
    except TokenValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        ) from exc

    # Attach user info to request state for downstream handlers.
    request.state.user = claims.get("sub", "")
    request.state.token_claims = claims
    return claims


# ── Service-to-service auth ───────────────────────────────────────────────────

async def require_service_key(request: Request) -> dict[str, Any]:
    """Verify ``X-Service-Key`` header for internal service-to-service calls.

    Only enforced when ``SERVICE_AUTH_KEY`` is configured in the environment.
    """
    from src.config import SERVICE_AUTH_KEY

    if not SERVICE_AUTH_KEY:
        return {}

    header_key = request.headers.get("X-Service-Key", "")
    if header_key != SERVICE_AUTH_KEY:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid service key",
        )
    return {}


# ── In-memory rate limiter ────────────────────────────────────────────────────

class SlidingWindowRateLimiter:
    """Simple in-memory sliding-window rate limiter (no Redis).

    Tracks request timestamps per client IP.  Old entries are pruned
    automatically on each check.
    """

    def __init__(self, max_requests: int = 10, window_seconds: int = 60) -> None:
        self._max_requests = max_requests
        self._window = window_seconds
        self._buckets: dict[str, list[float]] = defaultdict(list)

    def _prune(self, key: str, now: float) -> None:
        """Remove timestamps outside the sliding window."""
        cutoff = now - self._window
        bucket = self._buckets[key]
        while bucket and bucket[0] < cutoff:
            bucket.pop(0)
        if not bucket:
            del self._buckets[key]

    def check(self, key: str) -> float | None:
        """Check if *key* is within rate limits.

        Args:
            key: Typically the client IP address.

        Returns:
            ``None`` if the request is allowed, otherwise the number of
            seconds the client should wait (``Retry-After`` value).
        """
        now = time.monotonic()
        self._prune(key, now)
        bucket = self._buckets[key]

        if len(bucket) >= self._max_requests:
            # Time until the oldest entry exits the window.
            retry_after = bucket[0] + self._window - now
            return max(0.0, retry_after)

        bucket.append(now)
        return None


# Module-level rate limiter instance for auth endpoints.
auth_rate_limiter = SlidingWindowRateLimiter(max_requests=10, window_seconds=60)


async def rate_limit_auth(request: Request) -> None:
    """FastAPI dependency that applies rate-limiting to auth endpoints.

    Uses the client IP as the bucket key and returns a 429 with a
    ``Retry-After`` header when the limit is exceeded.
    """
    client_ip = request.client.host if request.client else "unknown"
    retry_after = auth_rate_limiter.check(client_ip)

    if retry_after is not None:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded",
            headers={"Retry-After": str(int(retry_after) + 1)},
        )
