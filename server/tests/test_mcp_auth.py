"""§5b Authorization acceptance test (.vision-documents/mcp-compliance-roadmap.md Phase 2).

Exercises mapcontrol_server.auth — the ESIP self-issued signed scoped-token
Resource Server for /mcp — by building a FastMCP instance from build_auth()
exactly as mcp_tools.py does, then driving it over Streamable HTTP. This is kept
independent of the shared app singleton in test_mcp.py (the MCP session manager's
run() is once-per-process), so both test modules coexist in one pytest run.

Asserts:
  * Default (no env) → auth OFF: build_auth() == (None, None) — ADR-0001.
  * Enabled but no secret → loud RuntimeError (no silent insecure mode).
  * With auth ON: /mcp initialize is 401 without a token, 200 with a valid
    self-issued token, 401 for expired / wrong-secret / wrong-audience /
    wrong-issuer tokens, and 403 when the token lacks the required scope.
  * The RFC 9728 Protected Resource Metadata document is served and well-formed.
  * 401 carries a WWW-Authenticate header pointing at the PRM document.

Runnable two ways:
  * `python tests/test_mcp_auth.py`   (standalone; prints PASS/FAIL, exits 1)
  * `pytest tests/test_mcp_auth.py`
"""

from __future__ import annotations

import importlib
import json
import os

from starlette.testclient import TestClient

RESOURCE = "https://esip.example.test/service/map"
SECRET = "unit-test-secret-please-keep-this-32+bytes-long"

HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}
INIT = {
    "jsonrpc": "2.0", "id": 1, "method": "initialize",
    "params": {"protocolVersion": "2025-06-18", "capabilities": {},
               "clientInfo": {"name": "test", "version": "0"}},
}


def _fresh_auth_module(**env):
    """Re-import mapcontrol_server.auth with a controlled environment.

    auth.py reads env lazily (at call time), but re-importing keeps each test's
    intent explicit and isolates module-level constants.
    """
    for k in list(os.environ):
        if k.startswith("MAPCONTROL_MCP_") or k == "MAPCONTROL_PUBLIC_URL":
            del os.environ[k]
    os.environ.update(env)
    import mapcontrol_server.auth as auth
    return importlib.reload(auth)


def _build_app(auth):
    """Build a FastMCP wired exactly like mcp_tools.py, with DNS-rebinding OFF so
    the TestClient's 'testserver' Host isn't rejected (that guard is independent
    of authorization and is covered by the Phase-1 transport-security path)."""
    from mcp.server.fastmcp import FastMCP
    from mcp.server.transport_security import TransportSecuritySettings

    verifier, settings = auth.build_auth()
    assert verifier is not None and settings is not None, "auth should be ON here"
    mcp = FastMCP(
        "ESIP Map Control (auth test)",
        stateless_http=True,
        token_verifier=verifier,
        auth=settings,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
    return mcp.streamable_http_app()


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


def test_auth_off_by_default():
    """ADR-0001: with no MAPCONTROL_MCP_AUTH, build_auth() is a no-op."""
    auth = _fresh_auth_module()  # nothing set
    assert auth.auth_enabled() is False
    assert auth.build_auth() == (None, None)


def test_auth_enabled_requires_secret():
    """Enabled but unconfigured must fail loudly, never silently run open."""
    auth = _fresh_auth_module(MAPCONTROL_MCP_AUTH="1", MAPCONTROL_MCP_RESOURCE=RESOURCE)
    raised = False
    try:
        auth.build_auth()
    except RuntimeError:
        raised = True
    assert raised, "build_auth() must raise when enabled without a secret"


def test_mcp_auth_enforced():
    auth = _fresh_auth_module(
        MAPCONTROL_MCP_AUTH="1",
        MAPCONTROL_MCP_AUTH_SECRET=SECRET,
        MAPCONTROL_MCP_RESOURCE=RESOURCE,
    )
    app = _build_app(auth)

    with TestClient(app) as client:
        # 1) No token → 401 with a WWW-Authenticate pointing at the PRM doc.
        r = _init(client)
        assert r.status_code == 401, r.text
        www = r.headers.get("www-authenticate", "")
        assert "Bearer" in www and "resource_metadata=" in www, www

        # 2) Valid self-issued token → 200.
        good = auth.issue_token(subject="eogpt-server", scopes=["mcp:use"])
        r = _init(client, good)
        assert r.status_code == 200, r.text
        body = _parse_sse(r.text)
        assert "result" in body, body

        # 3) Expired token → 401.
        expired = auth.issue_token(scopes=["mcp:use"], ttl_seconds=-10)
        assert _init(client, expired).status_code == 401

        # 4) Wrong signing secret → 401.
        forged = auth.issue_token(scopes=["mcp:use"], secret="a-different-secret-entirely")
        assert _init(client, forged).status_code == 401

        # 5) Missing required scope → 403 (authenticated but not authorized).
        wrong_scope = auth.issue_token(scopes=["some:other"])
        assert _init(client, wrong_scope).status_code == 403

        # 6) Garbage bearer → 401.
        assert _init(client, "not-a-jwt").status_code == 401

        # 7) PRM document is served + well-formed (RFC 9728).
        prm = client.get("/.well-known/oauth-protected-resource/service/map")
        assert prm.status_code == 200, prm.text
        doc = prm.json()
        assert doc["resource"] == RESOURCE
        assert "mcp:use" in doc.get("scopes_supported", [])


def test_token_audience_and_issuer_pinned():
    """A token minted for a different resource/issuer must be rejected (no token
    confusion / cross-RS replay)."""
    auth = _fresh_auth_module(
        MAPCONTROL_MCP_AUTH="1",
        MAPCONTROL_MCP_AUTH_SECRET=SECRET,
        MAPCONTROL_MCP_RESOURCE=RESOURCE,
    )
    app = _build_app(auth)

    import jwt
    import time as _t
    now = int(_t.time())
    # Correctly signed, but wrong audience + issuer.
    bad_aud = jwt.encode(
        {"iss": "https://evil.example", "sub": "x", "aud": "https://evil.example",
         "client_id": "x", "scope": "mcp:use", "iat": now, "nbf": now, "exp": now + 60},
        SECRET, algorithm="HS256",
    )
    with TestClient(app) as client:
        assert _init(client, bad_aud).status_code == 401


if __name__ == "__main__":
    test_auth_off_by_default()
    test_auth_enabled_requires_secret()
    test_mcp_auth_enforced()
    test_token_audience_and_issuer_pinned()
    print("RESULT: ALL PASSED", flush=True)
    # Force an immediate, clean exit — same reasoning as test_mcp.py: the MCP
    # session manager / TestClient portal can leave non-daemon threads alive
    # after the assertions pass, blocking interpreter shutdown and hanging
    # `python tests/test_mcp_auth.py` in CI. Everything's validated above.
    # (pytest does NOT enter this block.)
    os._exit(0)

