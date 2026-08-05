"""§5b Authorization for the MCP endpoint (.vision-documents/mcp-compliance-roadmap.md Phase 2).

ESIP becomes an OAuth 2.1 **Resource Server** on ``/mcp``: callers present a
bearer token, which a :class:`SelfIssuedTokenVerifier` validates. The default
token model is **ESIP self-issued, signed, scoped tokens** (HS256 JWT) — maximum
independence, no external IdP required for the pure-M2M production topology
(eogpt-server / eogpt-client → ESIP). The Resource-Server metadata is shaped so
an external Authorization Server (Cognito / Auth0 / Keycloak) can slot in later
by swapping the verifier + ``issuer_url`` with no other change.

Dual-deployability invariant (EOGPT-Roadmap ADR-0001): auth is **OFF by default**
(``MAPCONTROL_MCP_AUTH`` unset / != "1"). Local dev, docker-compose.local.yml,
direct-port and internal-M2M access are byte-for-byte unchanged. The CLOUD
env-file opts in by setting ``MAPCONTROL_MCP_AUTH=1`` + a secret. When OFF,
:func:`build_auth` returns ``(None, None)`` and FastMCP is constructed exactly as
in Phase 0/1.

Wiring (see ``mcp_tools.py``): ``FastMCP(token_verifier=..., auth=AuthSettings(...))``.
Given those, FastMCP automatically:
  * mounts the RFC 9728 Protected Resource Metadata at
    ``/.well-known/oauth-protected-resource{resource_path}``,
  * returns ``401`` + ``WWW-Authenticate: Bearer ... resource_metadata="…"`` on a
    missing/invalid token, and
  * returns ``403`` when the token lacks a required scope.

> Edge note (cloud): the PRM doc lives at the ORIGIN ROOT
> (``https://<host>/.well-known/oauth-protected-resource/service/map``). The
> single-origin edge currently routes origin-root ``/`` → Svelte, so BEFORE
> enabling auth in cloud the edge needs a location for
> ``/.well-known/oauth-protected-resource`` → ESIP (``172.17.0.1:8080``).
"""

from __future__ import annotations

import logging
import os
import time
import uuid

logger = logging.getLogger("mapcontrol")

# JWT signing algorithm for self-issued tokens. HS256 (shared secret) keeps the
# pure-M2M deployment dependency-free; an external AS would typically use RS256.
_ALG = "HS256"

# Default scope a token must carry to call /mcp. Kept deliberately coarse for the
# M2M phase; finer per-tool scopes can be layered in later (Phase 4+).
_DEFAULT_SCOPE = "mcp:use"


def _csv_env(name: str) -> list[str]:
    raw = os.environ.get(name, "")
    return [item.strip() for item in raw.split(",") if item.strip()]


def auth_enabled() -> bool:
    """True when MCP authorization is switched ON (cloud opt-in)."""
    return os.environ.get("MAPCONTROL_MCP_AUTH", "0") == "1"


def _resource_url() -> str:
    """The RS identifier == the public URL of this MCP server.

    Defaults to MAPCONTROL_MCP_RESOURCE, else MAPCONTROL_PUBLIC_URL (the same
    browser-facing base create_map uses), else localhost for dev. The path
    component (e.g. ``/service/map``) determines where FastMCP mounts the PRM
    document: ``/.well-known/oauth-protected-resource/service/map``.
    """
    return (
        os.environ.get("MAPCONTROL_MCP_RESOURCE")
        or os.environ.get("MAPCONTROL_PUBLIC_URL")
        or "http://localhost:8080"
    ).rstrip("/")


def _issuer_url() -> str:
    """Token issuer identifier. For self-issued tokens this is ESIP itself, so it
    defaults to the resource URL; override with MAPCONTROL_MCP_ISSUER when an
    external Authorization Server takes over issuance."""
    return (os.environ.get("MAPCONTROL_MCP_ISSUER") or _resource_url()).rstrip("/")


def _required_scopes() -> list[str]:
    scopes = _csv_env("MAPCONTROL_MCP_REQUIRED_SCOPES")
    return scopes or [_DEFAULT_SCOPE]


def _secret() -> str:
    """Shared signing secret. REQUIRED when auth is enabled — we refuse to run
    with an empty/guessable key so an 'enabled but unconfigured' deployment fails
    loudly instead of accepting unsigned-equivalent tokens."""
    secret = os.environ.get("MAPCONTROL_MCP_AUTH_SECRET", "")
    if auth_enabled() and not secret:
        raise RuntimeError(
            "MAPCONTROL_MCP_AUTH=1 but MAPCONTROL_MCP_AUTH_SECRET is unset. "
            "Set a strong (>=32 byte) shared secret, or unset MAPCONTROL_MCP_AUTH "
            "to run /mcp unauthenticated (Phase 0/1 behavior)."
        )
    return secret


# ─── Token issuance (for the M2M callers, tests, and a CLI helper) ────────────

def issue_token(
    subject: str = "eogpt",
    scopes: list[str] | tuple[str, ...] | None = None,
    ttl_seconds: int | None = None,
    secret: str | None = None,
) -> str:
    """Mint a signed, scoped bearer token for an M2M caller.

    Self-issued HS256 JWT with the standard registered claims plus a
    space-delimited ``scope`` (OAuth convention). The eogpt-server / eogpt-client
    callers obtain a token this way (shared secret) and send it as
    ``Authorization: Bearer <token>`` on their /mcp requests.
    """
    import jwt  # local import: only needed when auth is in use

    scopes = list(scopes) if scopes else _required_scopes()
    ttl = ttl_seconds if ttl_seconds is not None else int(
        os.environ.get("MAPCONTROL_MCP_TOKEN_TTL", "3600")
    )
    now = int(time.time())
    resource = _resource_url()
    payload = {
        "iss": _issuer_url(),
        "sub": subject,
        "aud": resource,
        "client_id": subject,
        "scope": " ".join(scopes),
        "jti": uuid.uuid4().hex,
        "iat": now,
        "nbf": now,
        "exp": now + ttl,
    }
    return jwt.encode(payload, secret or _secret(), algorithm=_ALG)


# ─── Token verification (the Resource-Server side) ────────────────────────────

class SelfIssuedTokenVerifier:
    """FastMCP ``TokenVerifier``: validate an ESIP self-issued HS256 JWT.

    Returns an ``AccessToken`` on success (FastMCP then enforces required scopes
    → 403 on mismatch) or ``None`` on any failure (FastMCP → 401). Validation
    checks signature, expiry (``exp``), not-before (``nbf``), audience (must equal
    this RS), and issuer.
    """

    def __init__(self) -> None:
        self._secret = _secret()
        self._issuer = _issuer_url()
        self._resource = _resource_url()

    async def verify_token(self, token: str):  # -> AccessToken | None
        import jwt
        from mcp.server.auth.provider import AccessToken

        try:
            payload = jwt.decode(
                token,
                self._secret,
                algorithms=[_ALG],
                audience=self._resource,
                issuer=self._issuer,
                options={"require": ["exp", "iat", "sub"]},
            )
        except Exception as exc:  # jwt.* errors → treat as unauthenticated
            logger.info("MCP token rejected: %s", type(exc).__name__)
            return None

        scope = payload.get("scope", "")
        scopes = scope.split() if isinstance(scope, str) else list(scope or [])
        return AccessToken(
            token=token,
            client_id=payload.get("client_id") or payload.get("sub", "unknown"),
            scopes=scopes,
            expires_at=payload.get("exp"),
            resource=payload.get("aud"),
            subject=payload.get("sub"),
            claims=payload,
        )


# ─── FastMCP wiring helper ────────────────────────────────────────────────────

def build_auth():
    """Return ``(token_verifier, auth_settings)`` for the ``FastMCP`` constructor.

    ``(None, None)`` when auth is disabled (the default) so FastMCP is built
    exactly as in Phase 0/1 — ADR-0001 dual-deployability. When enabled, returns
    a configured ``SelfIssuedTokenVerifier`` + ``AuthSettings`` that drive the PRM
    document, ``WWW-Authenticate`` on 401, and required-scope enforcement.
    """
    if not auth_enabled():
        return None, None

    from mcp.server.auth.settings import AuthSettings

    resource = _resource_url()
    issuer = _issuer_url()
    scopes = _required_scopes()
    verifier = SelfIssuedTokenVerifier()  # constructs → validates secret presence
    settings = AuthSettings(
        issuer_url=issuer,
        resource_server_url=resource,
        required_scopes=scopes,
    )
    logger.info(
        "MCP authorization ENABLED (resource=%s issuer=%s scopes=%s)",
        resource, issuer, scopes,
    )
    return verifier, settings
