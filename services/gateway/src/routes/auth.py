"""Authentication endpoints for the gateway.

Provides token exchange, refresh, and verification routes protected by
rate limiting. Revoked JWT identifiers are persisted to SQLite so they
survive process restarts.
"""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from src.auth import (
    ACCESS_TOKEN_TTL,
    TokenValidationError,
    create_access_token,
    create_refresh_token,
    decode_token,
    verify_token,
)
from src.config import AUTH_API_KEYS
from src.middleware import rate_limit_auth

router = APIRouter()

# ── SQLite-backed refresh-token revocation store ──────────────────────────────
# Persists ``jti`` values of revoked refresh tokens to ``data/auth.db``.
# Falls back to in-memory set if SQLite cannot be opened.

_auth_db: sqlite3.Connection | None = None
_fallback_jtis: set[str] = set()

try:
    _db_path = Path("data/auth.db")
    _db_path.parent.mkdir(parents=True, exist_ok=True)
    _auth_db = sqlite3.connect(str(_db_path), check_same_thread=False)
    _auth_db.execute("PRAGMA journal_mode=WAL")
    _auth_db.execute("PRAGMA synchronous=NORMAL")
    _auth_db.execute(
        """CREATE TABLE IF NOT EXISTS revoked_jtis (
            jti TEXT PRIMARY KEY,
            revoked_at TEXT NOT NULL
        )"""
    )
    _auth_db.commit()
except Exception:
    _auth_db = None


def _is_jti_revoked(jti: str) -> bool:
    """Check if a JTI has been revoked."""
    if _auth_db is not None:
        row = _auth_db.execute(
            "SELECT 1 FROM revoked_jtis WHERE jti = ?", (jti,)
        ).fetchone()
        return row is not None
    return jti in _fallback_jtis


def _revoke_jti(jti: str) -> None:
    """Add a JTI to the revocation store."""
    now = datetime.now(timezone.utc).isoformat()
    if _auth_db is not None:
        with _auth_db:
            _auth_db.execute(
                "INSERT OR IGNORE INTO revoked_jtis (jti, revoked_at) VALUES (?, ?)",
                (jti, now),
            )
    else:
        _fallback_jtis.add(jti)


# ── Request models ────────────────────────────────────────────────────────────

class TokenRequest(BaseModel):
    """Request body for ``POST /auth/token``."""

    api_key: str


class RefreshRequest(BaseModel):
    """Request body for ``POST /auth/refresh``."""

    refresh_token: str


class VerifyRequest(BaseModel):
    """Request body for ``POST /auth/verify``."""

    token: str


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/token")
async def exchange_token(
    body: TokenRequest,
    _rl: None = Depends(rate_limit_auth),
) -> dict[str, Any]:
    """Exchange a pre-shared API key for access + refresh JWT pair.

    Returns:
        Token response with ``access_token``, ``refresh_token``, and metadata.
    """
    if body.api_key not in AUTH_API_KEYS:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
        )

    sub = f"apikey:{hashlib.sha256(body.api_key.encode()).hexdigest()}"
    access = create_access_token(sub)
    refresh = create_refresh_token(sub)

    return {
        "access_token": access,
        "refresh_token": refresh,
        "token_type": "bearer",
        "expires_in": int(ACCESS_TOKEN_TTL.total_seconds()),
    }


@router.post("/refresh")
async def refresh_token(
    body: RefreshRequest,
    _rl: None = Depends(rate_limit_auth),
) -> dict[str, Any]:
    """Rotate a refresh token, issuing a new access + refresh pair.

    The old refresh token's ``jti`` is added to the revocation store
    so it cannot be reused (refresh token rotation).

    Returns:
        New token pair.
    """
    try:
        claims = decode_token(body.refresh_token, expected_type="refresh")
    except TokenValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        ) from exc

    jti = claims.get("jti", "")
    if _is_jti_revoked(jti):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token has been revoked",
        )

    # Revoke old refresh token.
    _revoke_jti(jti)

    sub = claims.get("sub", "")
    new_access = create_access_token(sub)
    new_refresh = create_refresh_token(sub)

    return {
        "access_token": new_access,
        "refresh_token": new_refresh,
        "token_type": "bearer",
        "expires_in": int(ACCESS_TOKEN_TTL.total_seconds()),
    }


@router.post("/verify")
async def verify_token_endpoint(
    body: VerifyRequest,
    _rl: None = Depends(rate_limit_auth),
) -> dict[str, Any]:
    """Verify whether a JWT is valid and return its decoded claims.

    Returns:
        Verification result with ``valid`` flag and decoded ``claims``.
    """
    try:
        claims = verify_token(body.token)
    except TokenValidationError:
        return {"valid": False, "claims": None}

    return {"valid": True, "claims": claims}
