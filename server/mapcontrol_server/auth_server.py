"""Demo OAuth 2.1 **Authorization Server** for the /mcp endpoint (toy AS).

Why this exists
---------------
§5b (``auth.py``) makes ESIP an OAuth **Resource Server** only: it validates a
bearer token that *something else* minted. That is exactly right for the
pure-M2M production topology (eogpt-server / eogpt-client mint their own
self-issued tokens and present them). It is NOT enough for a generic MCP client
like **Claude.ai (web) Custom Connectors**, which speaks the full OAuth 2.1
discovery + authorization-code-with-PKCE dance and has nowhere to paste a static
token:

    GET /mcp                      → 401 + WWW-Authenticate (RFC 9728 PRM)   [§5b]
    GET /.well-known/oauth-protected-resource{path}  → PRM doc              [§5b]
    GET /.well-known/oauth-authorization-server      → AS metadata (RFC 8414)  ← here
    POST /register                → Dynamic Client Registration (RFC 7591)  ← here
    GET  /authorize               → consent + auth code (PKCE S256)         ← here
    POST /token                   → code → access token                     ← here
    GET /mcp  (Bearer <token>)    → 200

This module supplies the missing Authorization-Server half as an **in-process,
in-memory, single-tenant toy** so the standalone ``esip-mcp`` demo box can be
added to Claude.ai as a Custom Connector and walk the whole flow end-to-end.

It is DEMO-ONLY and deliberately minimal:
  * In-memory stores (clients / codes / tokens) — wiped on restart, never shared
    across replicas. Fine for one demo container; NOT for production.
  * Opaque random access/refresh tokens (no JWT, no key management) validated by
    dict lookup. The Resource-Server check is ``load_access_token`` below.
  * Auto-approve consent by default; an optional static-password consent screen
    is enabled with ``MAPCONTROL_MCP_AS_CONSENT=1`` (+ ``..._PASSWORD``).

Dual-deployability invariant (EOGPT-Roadmap ADR-0001): the toy AS is **OFF by
default** (``MAPCONTROL_MCP_AS`` unset / != "1"). When OFF, ``build_auth_server``
returns ``(None, None)`` and FastMCP is constructed exactly as before — local
dev, docker-compose.local.yml, and the production/experimental ESIP are
byte-for-byte unchanged.

FUTURE / SCOPE NOTE (important): the **EOGPT-coupled** ESIP service must NOT grow
its own Authorization Server. Production ESIP stays a Resource Server (§5b) and
will defer to **EOGPT's future platform authentication** (a real AS / IdP — e.g.
the EOGPT login issuing scoped tokens, or Cognito/Auth0). This toy AS is only for
the *standalone* Claude-web demo box and is mutually exclusive with §5b on the
same instance (FastMCP forbids both ``auth_server_provider`` and
``token_verifier``). See .vision-documents/mcp-compliance-roadmap.md.
"""

from __future__ import annotations

import logging
import os
import secrets
import time
from html import escape

logger = logging.getLogger("mapcontrol")

# Coarse demo scope — mirrors auth.py so a token works against either auth path.
_DEFAULT_SCOPE = "mcp:use"

# Authorization codes are short-lived (RFC 6749 §10.5 recommends <=10 min).
_CODE_TTL_SECONDS = 300


def as_enabled() -> bool:
    """True when the toy Authorization Server is switched ON (demo-box opt-in)."""
    return os.environ.get("MAPCONTROL_MCP_AS", "0") == "1"


def _consent_enabled() -> bool:
    """Show the static consent screen instead of auto-approving."""
    return os.environ.get("MAPCONTROL_MCP_AS_CONSENT", "0") == "1"


def _consent_password() -> str:
    """Optional shared password gating the consent screen ("" = no password)."""
    return os.environ.get("MAPCONTROL_MCP_AS_CONSENT_PASSWORD", "")


def _public_base() -> str:
    """Public origin of this demo server (issuer == resource for a co-located AS).

    Defaults to MAPCONTROL_MCP_RESOURCE, else MAPCONTROL_PUBLIC_URL (the same
    browser-facing base create_map uses), else localhost for dev.
    """
    return (
        os.environ.get("MAPCONTROL_MCP_RESOURCE")
        or os.environ.get("MAPCONTROL_PUBLIC_URL")
        or "http://localhost:8000"
    ).rstrip("/")


def _token_ttl() -> int:
    return int(os.environ.get("MAPCONTROL_MCP_AS_TOKEN_TTL", "3600"))


# ─── Provider ─────────────────────────────────────────────────────────────────

class DemoAuthServerProvider:
    """In-memory ``OAuthAuthorizationServerProvider`` for the demo box.

    Implements the FastMCP provider Protocol (mcp.server.auth.provider). The SDK
    handlers do the protocol-level work (PKCE S256 verification, redirect_uri
    pinning, client-secret auth, code expiry); this class only stores/loads
    state and mints opaque tokens.
    """

    def __init__(self) -> None:
        # client_id -> OAuthClientInformationFull
        self.clients: dict[str, object] = {}
        # code -> AuthorizationCode
        self.auth_codes: dict[str, object] = {}
        # access_token -> AccessToken
        self.access_tokens: dict[str, object] = {}
        # refresh_token -> RefreshToken
        self.refresh_tokens: dict[str, object] = {}
        # consent transaction id -> (client, AuthorizationParams)
        self.txns: dict[str, tuple] = {}

    # --- Dynamic Client Registration (RFC 7591) ---------------------------

    async def get_client(self, client_id: str):
        return self.clients.get(client_id)

    async def register_client(self, client_info) -> None:
        self.clients[client_info.client_id] = client_info
        logger.info("Demo AS: registered client %s (%s)",
                    client_info.client_id, getattr(client_info, "client_name", None))

    # --- Authorization endpoint (RFC 6749 §4.1) ---------------------------

    async def authorize(self, client, params) -> str:
        """Return the URL the SDK's /authorize handler will 302 the client to.

        With consent OFF (default) we auto-approve: mint+store an authorization
        code and redirect straight back to the client's redirect_uri. With
        consent ON we redirect to our own /as/consent page, which calls
        :meth:`complete_consent` after the static-password gate.
        """
        if _consent_enabled():
            txn = secrets.token_urlsafe(32)
            self.txns[txn] = (client, params)
            return f"{_public_base()}/as/consent?txn={txn}"
        return self._issue_code(client, params)

    def _issue_code(self, client, params) -> str:
        from mcp.server.auth.provider import AuthorizationCode, construct_redirect_uri

        code = secrets.token_urlsafe(32)
        scopes = list(params.scopes) if params.scopes else [_DEFAULT_SCOPE]
        self.auth_codes[code] = AuthorizationCode(
            code=code,
            scopes=scopes,
            expires_at=time.time() + _CODE_TTL_SECONDS,
            client_id=client.client_id,
            code_challenge=params.code_challenge,
            redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            resource=params.resource,
        )
        # SEP-2468 / RFC 9207: include the issuer identifier in the
        # authorization response so clients can detect AS mix-up attacks by
        # validating `iss` against the issuer they discovered. Upcoming MCP
        # revisions require clients to validate this when present.
        return construct_redirect_uri(
            str(params.redirect_uri), code=code, state=params.state,
            iss=_public_base(),
        )

    # --- Consent screen support (optional) --------------------------------

    def render_consent_page(self, txn: str) -> str:
        known = txn in self.txns
        client = self.txns[txn][0] if known else None
        client_name = escape(str(getattr(client, "client_name", None) or "An MCP client"))
        pw_field = (
            '<label>Password<br><input type="password" name="password" autofocus></label><br><br>'
            if _consent_password()
            else ""
        )
        if not known:
            return (
                "<!doctype html><html><body style='font-family:Inter,system-ui;"
                "max-width:32rem;margin:4rem auto'><h2>Authorization request expired</h2>"
                "<p>Please restart the connection from your MCP client.</p></body></html>"
            )
        return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Authorize — ESIP Map Control</title></head>
<body style="font-family:Inter,system-ui,-apple-system,sans-serif;max-width:32rem;margin:4rem auto;color:#0f172a">
  <h2 style="display:flex;align-items:center;gap:.5rem">🗺️ ESIP Map Control</h2>
  <p><strong>{client_name}</strong> is requesting access to control live maps on
     your behalf (scope <code>{_DEFAULT_SCOPE}</code>).</p>
  <form method="post" action="/as/consent">
    <input type="hidden" name="txn" value="{escape(txn)}">
    {pw_field}
    <button type="submit" style="background:#5fa830;color:#fff;border:0;border-radius:6px;
            padding:.6rem 1.2rem;font-size:1rem;cursor:pointer">Approve</button>
  </form>
  <p style="color:#64748b;font-size:.85rem;margin-top:2rem">Demo authorization server — do not use for production secrets.</p>
</body></html>"""

    def complete_consent(self, txn: str, password: str) -> str | None:
        """Validate the consent submission; return the client redirect URL or None."""
        entry = self.txns.get(txn)
        if entry is None:
            return None
        required = _consent_password()
        if required and not secrets.compare_digest(required, password or ""):
            return None  # wrong password — caller renders an error
        del self.txns[txn]
        client, params = entry
        return self._issue_code(client, params)

    # --- Token endpoint (RFC 6749 §4.1.3) ---------------------------------

    async def load_authorization_code(self, client, authorization_code: str):
        code = self.auth_codes.get(authorization_code)
        if code is not None and code.client_id == client.client_id:
            return code
        return None

    async def exchange_authorization_code(self, client, authorization_code):
        from mcp.server.auth.provider import AccessToken, RefreshToken, TokenError
        from mcp.shared.auth import OAuthToken

        # One-time use: drop the code regardless of outcome.
        if self.auth_codes.pop(authorization_code.code, None) is None:
            raise TokenError("invalid_grant", "authorization code already used")

        ttl = _token_ttl()
        now = int(time.time())
        access = secrets.token_urlsafe(32)
        refresh = secrets.token_urlsafe(32)
        scopes = list(authorization_code.scopes)

        self.access_tokens[access] = AccessToken(
            token=access,
            client_id=client.client_id,
            scopes=scopes,
            expires_at=now + ttl,
            resource=authorization_code.resource,
        )
        self.refresh_tokens[refresh] = RefreshToken(
            token=refresh,
            client_id=client.client_id,
            scopes=scopes,
        )
        return OAuthToken(
            access_token=access,
            token_type="Bearer",
            expires_in=ttl,
            scope=" ".join(scopes),
            refresh_token=refresh,
        )

    # --- Refresh-token grant (RFC 6749 §6) --------------------------------

    async def load_refresh_token(self, client, refresh_token: str):
        rt = self.refresh_tokens.get(refresh_token)
        if rt is not None and rt.client_id == client.client_id:
            return rt
        return None

    async def exchange_refresh_token(self, client, refresh_token, scopes: list[str]):
        from mcp.server.auth.provider import AccessToken, RefreshToken
        from mcp.shared.auth import OAuthToken

        # Rotate both tokens (SDK recommendation).
        self.refresh_tokens.pop(refresh_token.token, None)
        granted = list(scopes) if scopes else list(refresh_token.scopes)
        ttl = _token_ttl()
        now = int(time.time())
        access = secrets.token_urlsafe(32)
        new_refresh = secrets.token_urlsafe(32)

        self.access_tokens[access] = AccessToken(
            token=access, client_id=client.client_id, scopes=granted, expires_at=now + ttl,
        )
        self.refresh_tokens[new_refresh] = RefreshToken(
            token=new_refresh, client_id=client.client_id, scopes=granted,
        )
        return OAuthToken(
            access_token=access, token_type="Bearer", expires_in=ttl,
            scope=" ".join(granted), refresh_token=new_refresh,
        )

    # --- Resource-Server verification path --------------------------------

    async def load_access_token(self, token: str):
        """Validate a bearer token (FastMCP wraps this as the TokenVerifier)."""
        at = self.access_tokens.get(token)
        if at is None:
            return None
        if at.expires_at and at.expires_at < int(time.time()):
            self.access_tokens.pop(token, None)
            return None
        return at

    async def revoke_token(self, token) -> None:
        self.access_tokens.pop(getattr(token, "token", ""), None)
        self.refresh_tokens.pop(getattr(token, "token", ""), None)


# ─── Module singleton + FastMCP wiring helper ─────────────────────────────────

# A single shared provider instance: mcp_tools.py hands it to FastMCP, and
# main.py's /as/consent routes drive the SAME instance (shared in-memory state).
_provider: DemoAuthServerProvider | None = None


def get_provider() -> DemoAuthServerProvider:
    global _provider
    if _provider is None:
        _provider = DemoAuthServerProvider()
    return _provider


def build_auth_server():
    """Return ``(auth_server_provider, auth_settings)`` for the FastMCP constructor.

    ``(None, None)`` when the toy AS is disabled (the default) so FastMCP is
    built exactly as before — ADR-0001 dual-deployability. When enabled, returns
    the in-memory provider + ``AuthSettings`` that turn ESIP into a self-contained
    OAuth 2.1 Authorization Server **and** Resource Server on this origin.
    """
    if not as_enabled():
        return None, None

    from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions

    base = _public_base()
    settings = AuthSettings(
        issuer_url=base,            # /authorize, /token, /register live here
        resource_server_url=base,   # the /mcp resource == same origin
        required_scopes=[_DEFAULT_SCOPE],
        client_registration_options=ClientRegistrationOptions(
            enabled=True,                       # Dynamic Client Registration (Claude needs it)
            valid_scopes=[_DEFAULT_SCOPE],
            default_scopes=[_DEFAULT_SCOPE],
        ),
    )
    logger.info(
        "Demo OAuth Authorization Server ENABLED (issuer=%s, consent=%s)",
        base, "on" if _consent_enabled() else "auto-approve",
    )
    return get_provider(), settings
