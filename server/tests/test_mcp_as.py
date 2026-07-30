"""Demo OAuth Authorization Server acceptance test (auth_server.py).

Exercises the toy in-process AS that lets a generic MCP client (e.g. Claude.ai
web) connect to /mcp via the full OAuth 2.1 flow. Builds a FastMCP from
build_auth_server() exactly as mcp_tools.py does, then drives the whole dance
over Streamable HTTP with a Starlette TestClient:

    /.well-known/oauth-authorization-server  (RFC 8414 AS metadata)
    POST /register                           (RFC 7591 Dynamic Client Registration)
    GET  /authorize  (PKCE S256)             → 302 back with ?code=… (auto-approve)
    POST /token      (code + verifier)       → access_token (+refresh)
    POST /mcp        (Bearer <token>)        → 200 initialize  / 401 without

Kept independent of the shared app singleton in test_mcp.py / test_mcp_auth.py
(the MCP session manager's run() is once-per-process) so all three modules
coexist in one pytest run.

Runnable two ways:
  * `python tests/test_mcp_as.py`   (standalone; prints PASS/FAIL, exits 1/0)
  * `pytest tests/test_mcp_as.py`
"""

from __future__ import annotations

import base64
import hashlib
import importlib
import json
import os
from urllib.parse import parse_qs, urlparse

from starlette.testclient import TestClient

BASE = "https://esip-mcp.example.test"

HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}
INIT = {
    "jsonrpc": "2.0", "id": 1, "method": "initialize",
    "params": {"protocolVersion": "2025-06-18", "capabilities": {},
               "clientInfo": {"name": "test", "version": "0"}},
}


def _fresh_as_module(**env):
    """Re-import mapcontrol_server.auth_server with a controlled environment.

    The provider holds in-memory state on a module singleton, so reloading
    guarantees each test starts from an empty store.
    """
    for k in list(os.environ):
        if k.startswith("MAPCONTROL_MCP_") or k == "MAPCONTROL_PUBLIC_URL":
            del os.environ[k]
    os.environ.update(env)
    import mapcontrol_server.auth_server as auth_server
    return importlib.reload(auth_server)


def _build_app(auth_server):
    """Build a FastMCP wired exactly like mcp_tools.py (AS path), with
    DNS-rebinding OFF so the TestClient 'testserver' Host isn't rejected."""
    from mcp.server.fastmcp import FastMCP
    from mcp.server.transport_security import TransportSecuritySettings

    provider, settings = auth_server.build_auth_server()
    assert provider is not None and settings is not None, "AS should be ON here"
    mcp = FastMCP(
        "ESIP Map Control (AS test)",
        stateless_http=True,
        auth_server_provider=provider,
        auth=settings,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
    return mcp.streamable_http_app()


def _pkce_pair():
    """Return (verifier, challenge) for PKCE S256."""
    verifier = base64.urlsafe_b64encode(os.urandom(40)).decode().rstrip("=")
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return verifier, challenge


def _parse_sse(text: str):
    for line in text.splitlines():
        if line.startswith("data:"):
            return json.loads(line[5:].strip())
    try:
        return json.loads(text)
    except Exception:
        return {"_raw": text[:200]}


def _init(client, token=None):
    h = dict(HEADERS)
    if token:
        h["Authorization"] = f"Bearer {token}"
    return client.post("/mcp", headers=h, content=json.dumps(INIT))


def test_as_off_by_default():
    """ADR-0001: with no MAPCONTROL_MCP_AS, build_auth_server() is a no-op."""
    auth_server = _fresh_as_module()  # nothing set
    assert auth_server.as_enabled() is False
    assert auth_server.build_auth_server() == (None, None)


def test_as_metadata_and_full_oauth_flow():
    auth_server = _fresh_as_module(
        MAPCONTROL_MCP_AS="1",
        MAPCONTROL_MCP_RESOURCE=BASE,
    )
    app = _build_app(auth_server)

    with TestClient(app, base_url=BASE) as client:
        # 0) Unauthenticated /mcp → 401 with a WWW-Authenticate pointer.
        r = _init(client)
        assert r.status_code == 401, r.text

        # 1) RFC 8414 AS metadata advertises the endpoints Claude discovers.
        meta = client.get("/.well-known/oauth-authorization-server")
        assert meta.status_code == 200, meta.text
        md = meta.json()
        assert md["issuer"].rstrip("/") == BASE
        for key in ("authorization_endpoint", "token_endpoint", "registration_endpoint"):
            assert key in md, (key, md)
        assert "S256" in md.get("code_challenge_methods_supported", [])

        # 2) Dynamic Client Registration (RFC 7591) — Claude registers itself.
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
        client_id = client_info["client_id"]
        client_secret = client_info["client_secret"]
        redirect_uri = "https://claude.ai/api/mcp/auth_callback"

        # 3) /authorize with PKCE → 302 back to redirect_uri with ?code=… (auto-approve).
        verifier, challenge = _pkce_pair()
        auth_resp = client.get("/authorize", params={
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": "xyz-state",
            "scope": "mcp:use",
        }, follow_redirects=False)
        assert auth_resp.status_code in (302, 307), auth_resp.text
        location = auth_resp.headers["location"]
        q = parse_qs(urlparse(location).query)
        assert location.startswith(redirect_uri), location
        assert q.get("state") == ["xyz-state"], q
        # SEP-2468 / RFC 9207: the authorization response carries the issuer
        # identifier so clients can detect AS mix-up attacks. The upcoming MCP
        # revision requires clients to validate `iss` when present.
        assert q.get("iss") == [BASE], q
        code = q["code"][0]

        # 4) /token exchange (code + PKCE verifier + client secret) → access token.
        tok = client.post("/token", data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "client_secret": client_secret,
            "code_verifier": verifier,
        })
        assert tok.status_code == 200, tok.text
        token_body = tok.json()
        access = token_body["access_token"]
        assert token_body["token_type"].lower() == "bearer"
        assert "mcp:use" in (token_body.get("scope") or "")

        # 5) Authenticated /mcp initialize → 200.
        r = _init(client, access)
        assert r.status_code == 200, r.text
        body = _parse_sse(r.text)
        assert "result" in body, body

        # 6) A garbage / unknown bearer is still rejected.
        assert _init(client, "not-a-real-token").status_code == 401

        # 7) Reusing the same authorization code must fail (one-time use).
        replay = client.post("/token", data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "client_secret": client_secret,
            "code_verifier": verifier,
        })
        assert replay.status_code == 400, replay.text


def test_pkce_mismatch_rejected():
    """A wrong PKCE verifier at /token must be rejected (RFC 7636 §4.6)."""
    auth_server = _fresh_as_module(
        MAPCONTROL_MCP_AS="1",
        MAPCONTROL_MCP_RESOURCE=BASE,
    )
    app = _build_app(auth_server)

    with TestClient(app, base_url=BASE) as client:
        reg = client.post("/register", json={
            "client_name": "PKCE Test",
            "redirect_uris": ["https://claude.ai/api/mcp/auth_callback"],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "client_secret_post",
            "scope": "mcp:use",
        })
        client_info = reg.json()
        redirect_uri = "https://claude.ai/api/mcp/auth_callback"

        _verifier, challenge = _pkce_pair()
        auth_resp = client.get("/authorize", params={
            "client_id": client_info["client_id"],
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "scope": "mcp:use",
        }, follow_redirects=False)
        code = parse_qs(urlparse(auth_resp.headers["location"]).query)["code"][0]

        # Wrong verifier (does not hash to the stored challenge).
        bad = client.post("/token", data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_info["client_id"],
            "client_secret": client_info["client_secret"],
            "code_verifier": "the-wrong-verifier-entirely-nope",
        })
        assert bad.status_code == 400, bad.text


if __name__ == "__main__":
    test_as_off_by_default()
    test_as_metadata_and_full_oauth_flow()
    test_pkce_mismatch_rejected()
    print("RESULT: ALL PASSED", flush=True)
    # Force an immediate, clean exit — same reasoning as test_mcp.py /
    # test_mcp_auth.py: the MCP session manager / TestClient portal can leave
    # non-daemon threads alive after the assertions pass, blocking interpreter
    # shutdown and hanging `python tests/test_mcp_as.py` in CI. Everything's
    # validated above. (pytest does NOT enter this block.)
    os._exit(0)
