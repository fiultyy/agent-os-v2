"""Authentication endpoints for the gateway.

Provides token exchange, refresh, and verification routes protected by
in-memory rate limiting.
"""

from __future__ import annotations

import hashlib
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

# ── In-memory refresh-token revocation store ──────────────────────────────────
# Tracks ``jti`` values of refresh tokens that have been used (rotated).
_revoked_jtis: set[str] = set()


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

    The old refresh token's ``jti`` is added to the in-memory revocation set
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
    if jti in _revoked_jtis:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token has been revoked",
        )

    # Revoke old refresh token.
    _revoked_jtis.add(jti)

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
