"""Standalone-auth portal for the ESIP MapControl MCP server.

This package implements the **Authed-Standalone** deployment mode: a
server-rendered web portal (login / signup / admin / dashboard) that gates the
in-process OAuth 2.1 Authorization Server with real, persistent user accounts.

Deployment modes (``MAPCONTROL_AUTH_MODE``)
-------------------------------------------
The server has three auth deployment modes, selected by one env var:

``local`` (default)
    No auth. Open ``/mcp``, no portal, no login. Byte-for-byte the Phase 0/1
    behavior — local dev, docker-compose.local.yml, and internal M2M access
    are unchanged (EOGPT-Roadmap ADR-0001 dual-deployability).

``standalone``
    This portal + the in-process OAuth AS. A person visiting ``/`` sees a
    login screen (or the dashboard when signed in). New users register at
    ``/signup`` and wait for approval; the ``admin`` user (and any user the
    admin promotes to *approver*) approves them from ``/admin``. MCP clients
    (Claude.ai web, Claude Code, ChatGPT, Cline, …) connect via the full
    OAuth 2.1 discovery + DCR + PKCE flow; the ``/authorize`` consent step is
    gated behind a logged-in, approved portal user.

``integrated``
    OAuth 2.1 **Resource Server** only (§5b, ``auth.py``): ESIP validates
    bearer tokens minted by an external authority (e.g. the EOGPT platform
    users system). No portal — the external system owns login/consent.

Back-compat: the pre-existing boolean knobs still work when
``MAPCONTROL_AUTH_MODE`` is unset — ``MAPCONTROL_MCP_AS=1`` keeps the *demo*
auto-approve AS (toy, in-memory; see ``auth_server.py``), and
``MAPCONTROL_MCP_AUTH=1`` keeps the §5b Resource Server. ``standalone`` is the
grown-up successor of the toy AS.

Extensibility (batteries included, swappable)
---------------------------------------------
The portal's persistence and credential checking sit behind two small
Protocols (``store.UserStore`` / ``store.CredentialVerifier``):

* Default store: SQLAlchemy (async) on SQLite at ``./data/portal.db`` — zero
  config. Point ``MAPCONTROL_USERS_DB_URL`` at Postgres/MySQL/… to swap the
  database with no code changes.
* Default verifier: scrypt password hashes (stdlib — no extra deps) against
  the user store.
* ``MAPCONTROL_AUTH_BACKEND=pkg.module:factory`` loads a custom
  ``(UserStore, CredentialVerifier)`` pair (LDAP, OIDC-password, …) without
  forking ESIP. Misconfiguration fails loudly at startup — never silently
  falls back to open.
"""

from __future__ import annotations

import os

_VALID_MODES = ("local", "standalone", "integrated")


def auth_mode() -> str:
    """Resolve the deployment auth mode.

    Precedence: explicit ``MAPCONTROL_AUTH_MODE`` → legacy boolean knobs →
    ``local``. An invalid explicit value fails loudly (never guess about an
    auth boundary).
    """
    explicit = os.environ.get("MAPCONTROL_AUTH_MODE", "").strip().lower()
    if explicit:
        if explicit not in _VALID_MODES:
            raise RuntimeError(
                f"MAPCONTROL_AUTH_MODE={explicit!r} is not one of {_VALID_MODES}. "
                "Refusing to start with an ambiguous auth configuration."
            )
        return explicit
    # Legacy knobs (kept working so existing deployments/tests are untouched).
    if os.environ.get("MAPCONTROL_MCP_AS", "0") == "1":
        return "standalone"  # AS on — but see portal_enabled(): the toy stays a toy
    if os.environ.get("MAPCONTROL_MCP_AUTH", "0") == "1":
        return "integrated"
    return "local"


def portal_enabled() -> bool:
    """True only for the EXPLICIT standalone mode.

    The legacy ``MAPCONTROL_MCP_AS=1`` (without ``MAPCONTROL_AUTH_MODE``)
    keeps the demo auto-approve AS without the portal, so the existing demo
    behavior and its CI gate (test_mcp_as.py) are byte-for-byte unchanged.
    """
    return os.environ.get("MAPCONTROL_AUTH_MODE", "").strip().lower() == "standalone"
