"""Standalone auth portal acceptance test (portal/).

Drives the FULL human + OAuth flow for MAPCONTROL_AUTH_MODE=standalone
against the real FastAPI app pieces:

    signup → pending (login blocked, 403)
    admin login → /admin approve → user login OK
    OAuth: DCR → /authorize → redirected to /consent → login-gated →
      approve (as approved user) → code → /token → authenticated /mcp 200
    consent DENY drops the transaction
    admin-only: approver role grant; approver can approve but not grant roles
    off-by-default: portal_enabled() False without MAPCONTROL_AUTH_MODE

Kept independent of the shared app singleton in test_mcp.py (the MCP session
manager's run() is once-per-process) — like test_mcp_as.py, this module builds
its own FastMCP + portal router in one Starlette app.

Runnable two ways:
  * `python tests/test_portal.py`   (standalone; prints PASS/FAIL, exits 1/0)
  * `pytest tests/test_portal.py`
"""

from __future__ import annotations

import base64
import hashlib
import importlib
import json
import os
import tempfile
from urllib.parse import parse_qs, urlparse

from starlette.testclient import TestClient

BASE = "https://esip-portal.example.test"

HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}
INIT = {
    "jsonrpc": "2.0", "id": 1, "method": "initialize",
    "params": {"protocolVersion": "2025-06-18", "capabilities": {},
               "clientInfo": {"name": "test", "version": "0"}},
}

ADMIN_PW = "correct-horse-battery-staple"


def _fresh_modules(**env):
    """Reload the portal modules with a controlled env + temp data dir."""
    for k in list(os.environ):
        if k.startswith("MAPCONTROL_"):
            del os.environ[k]
    tmp = tempfile.mkdtemp(prefix="esip-portal-test-")
    os.environ["MAPCONTROL_PORTAL_DATA_DIR"] = tmp
    os.environ.update(env)

    import mapcontrol_server.portal as portal_pkg
    import mapcontrol_server.portal.security as security
    import mapcontrol_server.auth_server as auth_server
    import mapcontrol_server.portal.provider as provider_mod
    import mapcontrol_server.portal.routes as routes_mod

    importlib.reload(portal_pkg)
    importlib.reload(security)
    security.reset_secret_cache()
    importlib.reload(auth_server)
    importlib.reload(provider_mod)
    importlib.reload(routes_mod)
    return portal_pkg, provider_mod, routes_mod


def _build_app(provider_mod, routes_mod):
    """One Starlette app = portal router + FastMCP (portal provider), wired
    like main.py does in standalone mode (portal startup + the MCP session
    manager's run() both live in the parent lifespan — mounting does NOT
    propagate the sub-app's lifespan)."""
    from contextlib import asynccontextmanager

    from fastapi import FastAPI
    from mcp.server.fastmcp import FastMCP
    from mcp.server.transport_security import TransportSecuritySettings

    provider, settings = provider_mod.build_portal_auth_server()
    assert provider is not None and settings is not None

    mcp = FastMCP(
        "ESIP Map Control (portal test)",
        stateless_http=True,
        auth_server_provider=provider,
        auth=settings,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )

    @asynccontextmanager
    async def lifespan(app):
        await routes_mod.portal_startup()
        async with mcp.session_manager.run():
            yield
        await routes_mod.portal_shutdown()

    app = FastAPI(lifespan=lifespan)
    app.include_router(routes_mod.router)
    app.mount("/", mcp.streamable_http_app())
    return app


def _pkce_pair():
    verifier = base64.urlsafe_b64encode(os.urandom(40)).decode().rstrip("=")
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return verifier, challenge


def _init(client, token=None):
    h = dict(HEADERS)
    if token:
        h["Authorization"] = f"Bearer {token}"
    return client.post("/mcp", headers=h, content=json.dumps(INIT))


def _login(client, username, password):
    return client.post("/login", data={"username": username, "password": password},
                       follow_redirects=False)


def test_portal_off_by_default():
    """No MAPCONTROL_AUTH_MODE → portal disabled; legacy knobs still map."""
    portal_pkg, _, _ = _fresh_modules()
    assert portal_pkg.portal_enabled() is False
    assert portal_pkg.auth_mode() == "local"
    os.environ["MAPCONTROL_MCP_AUTH"] = "1"
    assert portal_pkg.auth_mode() == "integrated"
    del os.environ["MAPCONTROL_MCP_AUTH"]
    os.environ["MAPCONTROL_MCP_AS"] = "1"
    assert portal_pkg.auth_mode() == "standalone"
    assert portal_pkg.portal_enabled() is False  # legacy AS ≠ portal
    del os.environ["MAPCONTROL_MCP_AS"]


def test_portal_full_flow():
    portal_pkg, provider_mod, routes_mod = _fresh_modules(
        MAPCONTROL_AUTH_MODE="standalone",
        MAPCONTROL_ADMIN_PASSWORD=ADMIN_PW,
        MAPCONTROL_MCP_RESOURCE=BASE,
        MAPCONTROL_USERS_DB_URL="sqlite+aiosqlite:///:memory:",
    )
    assert portal_pkg.portal_enabled() is True
    app = _build_app(provider_mod, routes_mod)

    with TestClient(app, base_url=BASE) as client:
        # ── Human portal surface ────────────────────────────────────────
        # 1) Anonymous / → login page.
        home = client.get("/")
        assert home.status_code == 200 and "Sign in" in home.text

        # 2) Unauthed /mcp → 401 (portal AS is a real RS too).
        assert _init(client).status_code == 401

        # 3) Signup → pending.
        r = client.post("/signup", data={
            "username": "alice", "password": "hunter2hunter2", "password2": "hunter2hunter2",
        })
        assert r.status_code == 200 and "pending" in r.text.lower()

        # 4) Pending user cannot log in.
        assert _login(client, "alice", "hunter2hunter2").status_code == 403

        # 5) Bad admin password rejected; good one sets a session cookie.
        assert _login(client, "admin", "wrong").status_code == 401
        r = _login(client, "admin", ADMIN_PW)
        assert r.status_code == 302
        assert "esip_portal_session" in r.cookies

        # 6) Admin dashboard reachable; admin panel lists alice as pending.
        dash = client.get("/")
        assert dash.status_code == 200 and "/mcp" in dash.text
        admin_page = client.get("/admin")
        assert admin_page.status_code == 200 and "alice" in admin_page.text

        # 7) Approve alice.
        r = client.post("/admin/users", data={"username": "alice", "action": "approve"})
        assert r.status_code == 200 and "approved" in r.text

        # 8) Admin grants approver role (admin-only action).
        r = client.post("/admin/users", data={"username": "alice", "action": "make_approver"})
        assert r.status_code == 200 and "approver" in r.text

        # 9) Log out; alice can now log in.
        client.get("/logout", follow_redirects=False)
        r = _login(client, "alice", "hunter2hunter2")
        assert r.status_code == 302

        # 10) Approver sees /admin but CANNOT grant roles (403).
        assert client.get("/admin").status_code == 200
        r = client.post("/signup", data={
            "username": "bob", "password": "password123", "password2": "password123",
        })
        # (signup works even while logged in — separate concern)
        r = client.post("/admin/users", data={"username": "bob", "action": "make_approver"})
        assert r.status_code == 403
        # …but CAN approve.
        r = client.post("/admin/users", data={"username": "bob", "action": "approve"})
        assert r.status_code == 200

        # ── OAuth flow gated by the portal ──────────────────────────────
        # 11) DCR (unchanged — protocol endpoint inside the MCP app).
        reg = client.post("/register", json={
            "client_name": "Claude Test",
            "redirect_uris": ["https://claude.ai/api/mcp/auth_callback"],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "client_secret_post",
            "scope": "mcp:use",
        })
        assert reg.status_code == 201, reg.text
        client_info = reg.json()
        redirect_uri = "https://claude.ai/api/mcp/auth_callback"

        # 12) /authorize NO LONGER auto-approves: it 302s to /consent.
        verifier, challenge = _pkce_pair()
        auth_resp = client.get("/authorize", params={
            "client_id": client_info["client_id"],
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": "xyz",
            "scope": "mcp:use",
        }, follow_redirects=False)
        assert auth_resp.status_code in (302, 307)
        loc = auth_resp.headers["location"]
        assert "/consent" in loc, loc
        txn = parse_qs(urlparse(loc).query)["txn"][0]

        # 13) Consent page shows the client; approve as the logged-in user.
        consent = client.get(f"/consent?txn={txn}")
        assert consent.status_code == 200 and "Claude Test" in consent.text
        approved = client.post("/consent", data={"txn": txn, "decision": "approve"},
                               follow_redirects=False)
        assert approved.status_code == 302
        cb = approved.headers["location"]
        assert cb.startswith(redirect_uri)
        q = parse_qs(urlparse(cb).query)
        assert q.get("state") == ["xyz"]
        code = q["code"][0]

        # 14) Token exchange → authenticated /mcp 200; token attributed to alice.
        tok = client.post("/token", data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_info["client_id"],
            "client_secret": client_info["client_secret"],
            "code_verifier": verifier,
        })
        assert tok.status_code == 200, tok.text
        access = tok.json()["access_token"]
        assert _init(client, access).status_code == 200
        assert provider_mod.get_portal_provider().subject_of(access) == "alice"

        # 15) Garbage bearer still rejected.
        assert _init(client, "not-a-token").status_code == 401

        # 16) DENY path: new authorize txn, deny → txn dropped (410 on reuse).
        _, challenge2 = _pkce_pair()
        auth2 = client.get("/authorize", params={
            "client_id": client_info["client_id"],
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "code_challenge": challenge2,
            "code_challenge_method": "S256",
            "scope": "mcp:use",
        }, follow_redirects=False)
        txn2 = parse_qs(urlparse(auth2.headers["location"]).query)["txn"][0]
        client.post("/consent", data={"txn": txn2, "decision": "deny"})
        assert client.get(f"/consent?txn={txn2}").status_code == 410

        # 17) Consent while logged OUT bounces to login.
        client.get("/logout", follow_redirects=False)
        r = client.get(f"/consent?txn=whatever", follow_redirects=False)
        assert r.status_code == 302 and r.headers["location"].startswith("/?next=")


def test_admin_password_required():
    """standalone mode without MAPCONTROL_ADMIN_PASSWORD must fail loudly."""
    portal_pkg, provider_mod, routes_mod = _fresh_modules(
        MAPCONTROL_AUTH_MODE="standalone",
        MAPCONTROL_MCP_RESOURCE=BASE,
        MAPCONTROL_USERS_DB_URL="sqlite+aiosqlite:///:memory:",
    )
    app = _build_app(provider_mod, routes_mod)
    try:
        with TestClient(app, base_url=BASE):
            raise AssertionError("startup should have failed without admin password")
    except RuntimeError as exc:
        assert "MAPCONTROL_ADMIN_PASSWORD" in str(exc)
    except Exception as exc:  # TestClient may wrap the startup error
        assert "MAPCONTROL_ADMIN_PASSWORD" in str(exc)


if __name__ == "__main__":
    test_portal_off_by_default()
    test_portal_full_flow()
    test_admin_password_required()
    print("RESULT: ALL PASSED", flush=True)
    # Force an immediate, clean exit — same reasoning as the other MCP suites:
    # the MCP session manager / TestClient portal can leave non-daemon threads
    # alive, blocking interpreter shutdown in CI. (pytest does NOT enter this.)
    os._exit(0)
