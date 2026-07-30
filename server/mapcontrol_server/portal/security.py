"""Password hashing + signed session cookies for the standalone portal.

Deliberately stdlib-only (``hashlib.scrypt`` + ``hmac``): no bcrypt/argon2
dependency, no compiled wheels, works everywhere the image runs. scrypt with
the parameters below is an OWASP-acceptable password KDF.

Sessions are stateless signed cookies (HMAC-SHA256 over
``username|role|expiry``), so no server-side session table is needed and a
container restart doesn't log everyone out — provided the session secret is
stable. The secret comes from ``MAPCONTROL_SESSION_SECRET``; when unset we
generate one and persist it next to the portal DB (batteries included), so
single-container deployments keep working with zero config.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from pathlib import Path

logger = logging.getLogger("mapcontrol")

# ─── Password hashing (scrypt, PHC-ish encoded string) ───────────────────────

_SCRYPT_N = 2**14  # CPU/memory cost
_SCRYPT_R = 8
_SCRYPT_P = 1
_SALT_BYTES = 16
_DKLEN = 64


def hash_password(password: str) -> str:
    """Return an encoded scrypt hash: ``scrypt$N$r$p$salt_b64$dk_b64``."""
    salt = os.urandom(_SALT_BYTES)
    dk = hashlib.scrypt(
        password.encode(), salt=salt,
        n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=_DKLEN,
    )
    return "scrypt${}${}${}${}${}".format(
        _SCRYPT_N, _SCRYPT_R, _SCRYPT_P,
        base64.b64encode(salt).decode(),
        base64.b64encode(dk).decode(),
    )


def verify_password(password: str, encoded: str) -> bool:
    """Constant-time check of ``password`` against an encoded scrypt hash."""
    try:
        scheme, n, r, p, salt_b64, dk_b64 = encoded.split("$")
        if scheme != "scrypt":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(dk_b64)
        candidate = hashlib.scrypt(
            password.encode(), salt=salt,
            n=int(n), r=int(r), p=int(p), dklen=len(expected),
        )
        return hmac.compare_digest(candidate, expected)
    except Exception:
        return False


# ─── Session cookies (HMAC-signed, stateless) ────────────────────────────────

SESSION_COOKIE = "esip_portal_session"
_DEFAULT_TTL = 60 * 60 * 24 * 7  # 7 days

_secret_cache: bytes | None = None


def _session_ttl() -> int:
    return int(os.environ.get("MAPCONTROL_SESSION_TTL", str(_DEFAULT_TTL)))


def _secret_file() -> Path:
    """Where the auto-generated secret persists (next to the portal DB)."""
    base = os.environ.get("MAPCONTROL_PORTAL_DATA_DIR", "./data")
    return Path(base) / ".portal_session_secret"


def session_secret() -> bytes:
    """The HMAC key for session cookies.

    Priority: ``MAPCONTROL_SESSION_SECRET`` env → persisted auto-generated
    file → generate + persist. Auto-generation keeps the batteries-included
    promise; explicitly setting the env var is recommended for multi-replica
    deployments.
    """
    global _secret_cache
    if _secret_cache is not None:
        return _secret_cache
    env = os.environ.get("MAPCONTROL_SESSION_SECRET", "")
    if env:
        _secret_cache = env.encode()
        return _secret_cache
    f = _secret_file()
    try:
        if f.exists():
            _secret_cache = f.read_bytes()
            if _secret_cache:
                return _secret_cache
        f.parent.mkdir(parents=True, exist_ok=True)
        _secret_cache = secrets.token_bytes(32)
        f.write_bytes(_secret_cache)
        try:
            f.chmod(0o600)
        except OSError:
            pass
        logger.info("Portal: generated a new session secret at %s", f)
        return _secret_cache
    except OSError as exc:
        # Can't persist (read-only fs?) — fall back to a process-lifetime key.
        logger.warning(
            "Portal: could not persist session secret (%s); sessions will not "
            "survive restarts. Set MAPCONTROL_SESSION_SECRET.", exc,
        )
        _secret_cache = secrets.token_bytes(32)
        return _secret_cache


def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _b64d(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def create_session_token(username: str, role: str) -> str:
    """Mint a signed session token: ``payload_b64.sig_b64``."""
    payload = json.dumps({
        "u": username,
        "r": role,
        "exp": int(time.time()) + _session_ttl(),
    }, separators=(",", ":")).encode()
    sig = hmac.new(session_secret(), payload, hashlib.sha256).digest()
    return f"{_b64e(payload)}.{_b64e(sig)}"


def read_session_token(token: str | None) -> dict | None:
    """Validate + decode a session token → ``{"u":…, "r":…}`` or ``None``."""
    if not token or "." not in token:
        return None
    try:
        payload_b64, sig_b64 = token.split(".", 1)
        payload = _b64d(payload_b64)
        expected = hmac.new(session_secret(), payload, hashlib.sha256).digest()
        if not hmac.compare_digest(_b64d(sig_b64), expected):
            return None
        data = json.loads(payload)
        if int(data.get("exp", 0)) < time.time():
            return None
        return data
    except Exception:
        return None


def reset_secret_cache() -> None:
    """Test helper: forget the cached secret so env changes take effect."""
    global _secret_cache
    _secret_cache = None
