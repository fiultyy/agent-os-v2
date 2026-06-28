"""JWT authentication utilities using RS256 asymmetric signing.

On startup the module loads (or auto-generates) an RSA key pair used for
signing and verifying JSON Web Tokens.  The public key can be safely shared
with other services that only need to verify tokens.

Keys are persisted to disk so they survive service restarts:
- If ``JWT_PRIVATE_KEY_PATH`` / ``JWT_PUBLIC_KEY_PATH`` are set, those paths are used.
- Otherwise keys are stored in ``data/keys/jwt_private.pem`` / ``data/keys/jwt_public.pem``
  (relative to the gateway service root).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import JWTError, jwt

from src.config import JWT_PRIVATE_KEY_PATH, JWT_PUBLIC_KEY_PATH

# ── Constants ─────────────────────────────────────────────────────────────────

ALGORITHM: str = "RS256"
ACCESS_TOKEN_TTL: timedelta = timedelta(minutes=15)
REFRESH_TOKEN_TTL: timedelta = timedelta(days=7)

# ── Key management ────────────────────────────────────────────────────────────

_private_key_pem: str | None = None
_public_key_pem: str | None = None

# Default key storage directory (relative to this file's location)
_GATEWAY_ROOT = Path(__file__).parent.parent
_DEFAULT_KEY_DIR = _GATEWAY_ROOT / "data" / "keys"
_DEFAULT_PRIVATE_PATH = _DEFAULT_KEY_DIR / "jwt_private.pem"
_DEFAULT_PUBLIC_PATH = _DEFAULT_KEY_DIR / "jwt_public.pem"


def _generate_key_pair() -> tuple[str, str]:
    """Generate a fresh 2048-bit RSA key pair and return PEM strings."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    priv_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()

    pub_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()

    return priv_pem, pub_pem


def _save_keys(priv_pem: str, pub_pem: str, priv_path: Path, pub_path: Path) -> None:
    """Persist a key pair to the given file paths, creating parent dirs as needed."""
    priv_path.parent.mkdir(parents=True, exist_ok=True)
    priv_path.write_text(priv_pem, encoding="utf-8")
    pub_path.write_text(pub_pem, encoding="utf-8")


def _load_or_generate_keys() -> tuple[str, str]:
    """Load keys from configured or default paths, generating and saving if absent."""
    # Resolve key paths: use env-configured paths, otherwise fall back to defaults
    priv_path_str = JWT_PRIVATE_KEY_PATH or str(_DEFAULT_PRIVATE_PATH)
    pub_path_str = JWT_PUBLIC_KEY_PATH or str(_DEFAULT_PUBLIC_PATH)
    priv_path = Path(priv_path_str)
    pub_path = Path(pub_path_str)

    # Load from file if both files exist
    if priv_path.exists() and pub_path.exists():
        priv = priv_path.read_text(encoding="utf-8")
        pub = pub_path.read_text(encoding="utf-8")
        return priv, pub

    # Generate new key pair
    priv_pem, pub_pem = _generate_key_pair()

    # Persist to the resolved paths (whether env-configured or default)
    _save_keys(priv_pem, pub_pem, priv_path, pub_path)

    return priv_pem, pub_pem


def init_keys() -> None:
    """Initialise the signing keys (call once at startup)."""
    global _private_key_pem, _public_key_pem
    _private_key_pem, _public_key_pem = _load_or_generate_keys()


def get_private_key() -> str:
    """Return the PEM-encoded private key. Raises if keys not initialised."""
    if _private_key_pem is None:
        raise RuntimeError("Auth keys not initialised — call init_keys() first")
    return _private_key_pem


def get_public_key() -> str:
    """Return the PEM-encoded public key. Raises if keys not initialised."""
    if _public_key_pem is None:
        raise RuntimeError("Auth keys not initialised — call init_keys() first")
    return _public_key_pem


# ── Token creation ────────────────────────────────────────────────────────────

def create_access_token(sub: str) -> str:
    """Create a short-lived access token.

    Args:
        sub: Subject identifier (typically the API key hash or user id).

    Returns:
        Encoded JWT string.
    """
    now = datetime.now(timezone.utc)
    claims: dict[str, Any] = {
        "sub": sub,
        "exp": now + ACCESS_TOKEN_TTL,
        "iat": now,
        "type": "access",
        "jti": str(uuid.uuid4()),
    }
    return jwt.encode(claims, get_private_key(), algorithm=ALGORITHM)


def create_refresh_token(sub: str) -> str:
    """Create a long-lived refresh token.

    Args:
        sub: Subject identifier.

    Returns:
        Encoded JWT string.
    """
    now = datetime.now(timezone.utc)
    claims: dict[str, Any] = {
        "sub": sub,
        "exp": now + REFRESH_TOKEN_TTL,
        "iat": now,
        "type": "refresh",
        "jti": str(uuid.uuid4()),
    }
    return jwt.encode(claims, get_private_key(), algorithm=ALGORITHM)


# ── Token validation ──────────────────────────────────────────────────────────

class TokenValidationError(Exception):
    """Raised when a JWT cannot be decoded or has invalid claims."""


def decode_token(token: str, *, expected_type: str | None = None) -> dict[str, Any]:
    """Decode and validate a JWT.

    Args:
        token: The raw JWT string.
        expected_type: If set, enforce that the ``type`` claim matches.

    Returns:
        The token claims dict.

    Raises:
        TokenValidationError: On any validation failure.
    """
    try:
        claims = jwt.decode(token, get_public_key(), algorithms=[ALGORITHM])
    except JWTError as exc:
        raise TokenValidationError(f"Invalid token: {exc}") from exc

    if expected_type and claims.get("type") != expected_type:
        raise TokenValidationError(
            f"Expected token type '{expected_type}', got '{claims.get('type')}'"
        )

    return claims


def verify_token(token: str) -> dict[str, Any]:
    """Verify any valid token (access or refresh) and return claims.

    Raises:
        TokenValidationError: On any validation failure.
    """
    return decode_token(token)
