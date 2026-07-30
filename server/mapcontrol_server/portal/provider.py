"""Portal-gated OAuth 2.1 Authorization-Server provider (standalone mode).

Extends the demo provider (``auth_server.DemoAuthServerProvider``) — same
protocol mechanics (DCR, PKCE, code/token exchange handled by the MCP SDK) —
but the ``/authorize`` step is **never** auto-approved. Instead the user-agent
is redirected to the portal's ``/consent`` page, which requires a logged-in,
**approved** portal account before the authorization code is minted. Issued
tokens carry the approving username so access is attributable per-user.

State model (v1, deliberate): the *user accounts* are persistent (the portal
store); the OAuth client registrations / codes / tokens remain in-memory. A
container restart therefore invalidates outstanding MCP-client tokens — the
clients simply re-run the OAuth flow (401 → discovery → authorize), which
every conforming MCP client handles automatically. Persisting token state is
a straightforward follow-up if restart-transparency ever matters.
"""

from __future__ import annotations

import logging
import secrets
from urllib.parse import parse_qs, urlparse

from ..auth_server import DemoAuthServerProvider, _public_base, _DEFAULT_SCOPE

logger = logging.getLogger("mapcontrol")


class PortalAuthServerProvider(DemoAuthServerProvider):
    """Authorization requires a real portal login + explicit consent."""

    async def authorize(self, client, params) -> str:
        """Always route through the portal consent page (no auto-approve)."""
        txn = secrets.token_urlsafe(32)
        self.txns[txn] = (client, params)
        return f"{_public_base()}/consent?txn={txn}"

    def peek_txn(self, txn: str):
        """Return ``(client, params)`` for an in-flight transaction, or None."""
        return self.txns.get(txn)

    def approve(self, txn: str, username: str) -> str | None:
        """Consent granted by a logged-in approved user → mint the code.

        Returns the client redirect URL, or ``None`` for an unknown/expired
        transaction. The username is recorded on the issued tokens.
        """
        entry = self.txns.pop(txn, None)
        if entry is None:
            return None
        client, params = entry
        redirect_url = self._issue_code(client, params)
        # Remember who approved this code so the token exchange can attribute
        # the access token. The code is embedded in the redirect URL query.
        code_values = parse_qs(urlparse(redirect_url).query).get("code", [])
        if code_values:
            self._code_subjects[code_values[0]] = username
        logger.info("Portal: user %r approved OAuth client %r",
                    username, getattr(client, "client_name", client.client_id))
        return redirect_url

    def deny(self, txn: str) -> None:
        """Consent denied — drop the transaction."""
        self.txns.pop(txn, None)

    # ── Subject attribution ────────────────────────────────────────────────

    def __init__(self) -> None:
        super().__init__()
        # authorization code -> portal username that approved it
        self._code_subjects: dict[str, str] = {}
        # access/refresh token -> portal username
        self._token_subjects: dict[str, str] = {}

    async def exchange_authorization_code(self, client, authorization_code):
        subject = self._code_subjects.pop(authorization_code.code, None)
        token = await super().exchange_authorization_code(client, authorization_code)
        if subject:
            self._token_subjects[token.access_token] = subject
            if token.refresh_token:
                self._token_subjects[token.refresh_token] = subject
        return token

    async def exchange_refresh_token(self, client, refresh_token, scopes):
        subject = self._token_subjects.pop(refresh_token.token, None)
        token = await super().exchange_refresh_token(client, refresh_token, scopes)
        if subject:
            self._token_subjects[token.access_token] = subject
            if token.refresh_token:
                self._token_subjects[token.refresh_token] = subject
        return token

    def subject_of(self, token: str) -> str | None:
        """The portal username a bearer token was issued to (if known)."""
        return self._token_subjects.get(token)


# ─── Singleton + FastMCP wiring (mirrors auth_server.build_auth_server) ──────

_provider: PortalAuthServerProvider | None = None


def get_portal_provider() -> PortalAuthServerProvider:
    global _provider
    if _provider is None:
        _provider = PortalAuthServerProvider()
    return _provider


def build_portal_auth_server():
    """Return ``(auth_server_provider, auth_settings)`` for standalone mode."""
    from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions

    base = _public_base()
    settings = AuthSettings(
        issuer_url=base,
        resource_server_url=base,
        required_scopes=[_DEFAULT_SCOPE],
        client_registration_options=ClientRegistrationOptions(
            enabled=True,
            valid_scopes=[_DEFAULT_SCOPE],
            default_scopes=[_DEFAULT_SCOPE],
        ),
    )
    logger.info("Standalone auth portal ENABLED (issuer=%s, consent=portal-login)", base)
    return get_portal_provider(), settings
