"""SPIKE (not a test — deliberately named spike_* so pytest/CI never collect
it): minimal MCPServer from the mcp v2 beta over Streamable HTTP.

Phase 5 migration reconnaissance for the 2026 protocol revision. Findings are
recorded in .vision-documents/mcp-2026-protocol-upgrade.md §7. Re-run against each new
v2 pre-release (b3/rc/stable, expected 2026-07-28) to track when
server/discover lands.

Run in a THROWAWAY venv — do NOT install the v2 beta into the project venv:

    python3 -m venv /tmp/mcp2-spike-venv
    /tmp/mcp2-spike-venv/bin/pip install "mcp==2.0.0b2" starlette
    /tmp/mcp2-spike-venv/bin/python tests/spike_mcp_v2.py

Probes / records (b2 results in parentheses):
  * server/discover round-trip                 (b2: -32601 Method not found)
  * tools/call WITHOUT initialize, stateless   (b2: works ✅)
  * required Mcp-Method / Mcp-Name headers     (sent; accepted)
  * native resultType / ttlMs / cacheScope     (model fields exist; not on the
                                                wire for legacy requests)
  * legacy initialize round-trip               (b2: works ✅ — dual-revision,
                                                but re-advertises prompts/
                                                resources/experimental →
                                                wrapper must be re-ported)
  * v2 flips DNS-rebinding protection ON by default (421 without opt-out)
"""
import json

from starlette.testclient import TestClient

from mcp.server import MCPServer, CacheHint
from mcp.server.transport_security import TransportSecuritySettings

srv = MCPServer(
    name="spike",
    version="0.0.1",
    cache_hints={"tools/list": CacheHint(ttl_ms=3_600_000, scope="public")},
)


@srv.tool()
def ping_tool(x: int) -> dict:
    """Return x+1."""
    return {"y": x + 1}


# NOTE (finding): v2 enables DNS-rebinding protection BY DEFAULT (421 on
# unknown Host) — the opposite default from 1.x. stateless_http also moved
# from the constructor to streamable_http_app().
app = srv.streamable_http_app(
    stateless_http=True,
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=False,
    ),
)

H = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}


def sse(text):
    for line in text.splitlines():
        if line.startswith("data:"):
            return json.loads(line[5:].strip())
    try:
        return json.loads(text)
    except Exception:
        return {"_raw": text[:400]}


def post(client, payload, extra_headers=None):
    h = dict(H)
    if payload.get("method"):
        h["Mcp-Method"] = payload["method"]
        name = (payload.get("params") or {}).get("name")
        if name:
            h["Mcp-Name"] = name
    if extra_headers:
        h.update(extra_headers)
    return client.post("/mcp", headers=h, content=json.dumps(payload))


with TestClient(app) as client:
    # 1) server/discover
    r = post(client, {"jsonrpc": "2.0", "id": 1, "method": "server/discover", "params": {}})
    print("discover status:", r.status_code)
    body = sse(r.text)
    print("discover result:", json.dumps(body, indent=2)[:800])

    # 2) tools/call with NO initialize; _meta carries protocol version
    r2 = post(client, {
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {
            "name": "ping_tool",
            "arguments": {"x": 41},
            "_meta": {
                "io.modelcontextprotocol/protocolVersion": "DRAFT",
                "io.modelcontextprotocol/clientCapabilities": {},
                "io.modelcontextprotocol/clientInfo": {"name": "spike", "version": "0"},
            },
        },
    })
    print("tools/call (no init) status:", r2.status_code)
    print("tools/call result:", json.dumps(sse(r2.text), indent=2)[:700])

    # 3) tools/list — native ttlMs/cacheScope/resultType?
    r3 = post(client, {"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {}})
    tl = sse(r3.text)
    res = tl.get("result", {})
    print("tools/list keys:", sorted(res.keys()))

    # 4) LEGACY initialize — dual-revision support?
    r4 = post(client, {
        "jsonrpc": "2.0", "id": 4, "method": "initialize",
        "params": {"protocolVersion": "2025-11-25", "capabilities": {},
                   "clientInfo": {"name": "legacy", "version": "0"}},
    })
    print("legacy initialize status:", r4.status_code)
    print("legacy initialize body:", json.dumps(sse(r4.text))[:400])

import os
os._exit(0)
