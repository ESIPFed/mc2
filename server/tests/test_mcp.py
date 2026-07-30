"""Phase 0 MCP conformance / acceptance test (docs/vision/mcp-compliance-roadmap.md).

Boots the real FastAPI app (with the MCP layer mounted at /mcp) against a
throwaway SQLite DB and asserts:

  * REST surface still works (health, create map).
  * MCP `initialize` succeeds over Streamable HTTP (protocol 2025-06-18).
  * Version negotiation: requesting `2025-11-25` (the current spec revision)
    is echoed back verbatim, and an unknown/future version falls back to a
    server-supported one (closes the Phase-3 known gap; see
    docs/vision/mcp-2026-protocol-upgrade.md).
  * 2026 draft-revision pre-adoption (mcp_compat.py): results carry
    `resultType: "complete"` (SEP-2322), `ttlMs`/`cacheScope` on cacheable
    results (SEP-2549), server identity in `_meta` (SEP-2575 SHOULD);
    resource-not-found reads return `-32602` (SEP-2164); the harness sends
    `Mcp-Method`/`Mcp-Name` headers (SEP-2243); tools/list order is
    deterministic.
  * Advertised capabilities == implemented: `tools` AND `resources` (the §6
    Resources layer) are advertised; prompts/experimental remain suppressed;
    the MCP Apps extension (`io.modelcontextprotocol/ui`) is advertised in
    `capabilities.extensions` (mcp_apps.py; docs/vision/mcp-apps-appification.md).
  * `tools/list` returns exactly the 14 tools (12 consolidated + set_theme
    + show_map).
  * MCP Apps (ext-apps / SEP-1865): UI-enabled tools (create_map, show_map)
    carry `_meta.ui.resourceUri` (nested form) pointing at ui://esip/map.html;
    the template reads back as `text/html;profile=mcp-app` with
    `_meta.ui.csp.frameDomains`; ui:// resources are hidden from
    `resources/list`; show_map round-trips.

  * `tools/call create_map` then `tools/call add_geojson` create an asset, and
    the REST asset list sees it (proving MCP and REST share the same state).
  * Malformed GeoJSON returns a Tool Execution Error (`isError`), NOT a
    JSON-RPC protocol error (report SEP-1303).
  * §6 Resources: `resources/templates/list` returns the full map:// taxonomy;
    `resources/read` round-trips JSON (assets index), application/geo+json
    (asset geometry), and binary image/png (screenshot blob); mutating tools
    return `resource_link` content blocks; unknown map/asset reads error
    without crashing the protocol.
  * `/map/{id}` still serves HTML (the live map is untouched).

Runnable two ways:
  * `python tests/test_mcp.py`         (standalone; prints PASS/FAIL, exits 1 on failure)
  * `pytest tests/test_mcp.py`         (the `test_mcp_phase0` function is collected)
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile

# Throwaway DB + file dir BEFORE importing the app (config reads env at import).
_tmp = tempfile.mkdtemp(prefix="mcp_test_")
os.environ.setdefault("MAPCONTROL_DB_PATH", os.path.join(_tmp, "test.db"))
os.environ.setdefault("MAPCONTROL_FILE_DIR", os.path.join(_tmp, "files"))

from starlette.requests import Request  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

from mapcontrol_server import database  # noqa: E402
from mapcontrol_server.config import load_config  # noqa: E402
from mapcontrol_server.main import app, serve_map  # noqa: E402
from mapcontrol_server.services import session_service  # noqa: E402


def _render_map_html(root_path: str = "") -> str:
    """Render the inline map HTML for the root-path tests by driving serve_map
    DIRECTLY (no TestClient). The MCP Streamable-HTTP session manager's run() is
    a once-per-process singleton, so only ONE test in this module may enter the
    app lifespan (`with TestClient(app)` in test_mcp_phase0). These rendering
    tests therefore bypass the app/ASGI stack entirely: they init the DB, create
    a real map, then call the route coroutine with a synthetic request scope
    carrying the ASGI `root_path` a reverse proxy / uvicorn --root-path would set.
    Starlette derives request.base_url from that scope, so this exercises the
    exact prefixing logic without a running server or proxy."""
    if database._db is None:
        asyncio.run(database.init_db(load_config()))

    async def _run() -> str:
        created = await session_service.create_map("http://testserver")
        scope = {
            "type": "http",
            "method": "GET",
            "path": f"/map/{created.map_id}",
            "raw_path": f"/map/{created.map_id}".encode(),
            "root_path": root_path,
            "query_string": b"",
            "headers": [(b"host", b"testserver")],
            "scheme": "http",
            "server": ("testserver", 80),
            "client": ("testclient", 50000),
        }
        response = await serve_map(created.map_id, Request(scope))
        return response.body.decode()

    return asyncio.run(_run())



HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}

EXPECTED_TOOLS = {
    "create_map", "add_geojson", "add_arc", "add_geotiff", "zoom",
    "list_assets", "delete_asset", "set_visibility", "update_style",
    "set_basemap", "set_theme", "get_viewport", "take_screenshot",
    "show_map",
}

# MCP Apps layer (mcp_apps.py)
UI_TEMPLATE_URI = "ui://esip/map.html"
UI_MIME_TYPE = "text/html;profile=mcp-app"
UI_ENABLED_TOOLS = {"create_map", "show_map"}

# The §6 Resources taxonomy (mcp_resources.py). map://maps is a CONCRETE
# resource (resources/list); the rest are templates (resources/templates/list).
EXPECTED_RESOURCE_TEMPLATES = {
    "map://{map_id}",
    "map://{map_id}/viewport",
    "map://{map_id}/assets",
    "map://{map_id}/assets/{asset_id}",
    "map://{map_id}/assets/{asset_id}/style",
    "map://{map_id}/drawn",
    "map://{map_id}/rasters/{asset_id}",
    "map://{map_id}/screenshots/{screenshot_id}",
}


def _parse_sse(text: str):
    """Streamable HTTP returns an SSE frame; pull the JSON out of the data: line."""
    for line in text.splitlines():
        if line.startswith("data:"):
            return json.loads(line[5:].strip())
    try:
        return json.loads(text)
    except Exception:
        return {"_raw": text[:300]}


def _mcp(client: TestClient, sid, payload: dict):
    h = dict(HEADERS)
    if sid:
        h["Mcp-Session-Id"] = sid
    # SEP-2243 (2026 draft revision): standard MCP request headers on every
    # Streamable HTTP POST. Harmless extra headers on the current revision;
    # REQUIRED once the new revision ships — the harness sends them now so
    # the gate exercises the future-conformant client shape.
    method = payload.get("method")
    if method:
        h["Mcp-Method"] = method
        params = payload.get("params") or {}
        name = params.get("name")
        if name:
            h["Mcp-Name"] = name
    return client.post("/mcp", headers=h, content=json.dumps(payload))


def _structured(result: dict) -> dict:
    sc = result.get("structuredContent")
    if sc:
        return sc
    for c in result.get("content", []):
        if c.get("type") == "text":
            try:
                return json.loads(c["text"])
            except Exception:
                pass
    return {}


def test_mcp_phase0():
    with TestClient(app) as client:
        # REST still works
        assert client.get("/health").status_code == 200
        assert client.post("/api/maps").status_code == 201

        # initialize
        init = _mcp(client, None, {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                       "clientInfo": {"name": "test", "version": "0"}},
        })
        assert init.status_code == 200, init.text
        sid = init.headers.get("mcp-session-id")

        # Advertised == implemented: initialize must advertise `tools` AND
        # `resources` (the §6 Resources layer is live), and NOT prompts /
        # experimental (still unimplemented — Phase 1 invariant holds).
        init_body = _parse_sse(init.text)
        caps = init_body.get("result", {}).get("capabilities", {})
        assert "tools" in caps, caps
        assert "resources" in caps, f"resources not advertised: {caps}"
        # Subscriptions are deferred; the capability must not claim them.
        assert not (caps.get("resources") or {}).get("subscribe"), caps
        assert "prompts" not in caps, f"over-advertised prompts: {caps}"
        assert "experimental" not in caps, f"over-advertised experimental: {caps}"
        # MCP Apps extension advertised (mcp_apps.py) — SEP-2133 negotiation.
        _ui_ext = (caps.get("extensions") or {}).get("io.modelcontextprotocol/ui")
        assert _ui_ext, f"apps extension not advertised: {caps}"
        assert UI_MIME_TYPE in _ui_ext.get("mimeTypes", []), _ui_ext

        _mcp(client, sid, {"jsonrpc": "2.0", "method": "notifications/initialized"})

        # ── Version negotiation (Phase-3 known gap, closed 2026-07-16) ──────
        # The 2026 draft revision (SEP-2575) will replace initialize entirely;
        # until then the gate must pin the FULL currently-supported set, not
        # just 2025-06-18. See docs/vision/mcp-2026-protocol-upgrade.md §5.
        # Requesting the current spec revision must be echoed back verbatim.
        init_1125 = _parse_sse(_mcp(client, None, {
            "jsonrpc": "2.0", "id": 30, "method": "initialize",
            "params": {"protocolVersion": "2025-11-25", "capabilities": {},
                       "clientInfo": {"name": "test", "version": "0"}},
        }).text)
        negotiated = init_1125.get("result", {}).get("protocolVersion")
        assert negotiated == "2025-11-25", init_1125

        # An unknown/future version must NOT be echoed; the server falls back
        # to a version it actually supports (SDK: LATEST_PROTOCOL_VERSION).
        from mcp.shared.version import SUPPORTED_PROTOCOL_VERSIONS
        init_future = _parse_sse(_mcp(client, None, {
            "jsonrpc": "2.0", "id": 31, "method": "initialize",
            "params": {"protocolVersion": "2099-01-01", "capabilities": {},
                       "clientInfo": {"name": "test", "version": "0"}},
        }).text)
        fallback = init_future.get("result", {}).get("protocolVersion")
        assert fallback != "2099-01-01", init_future
        assert fallback in SUPPORTED_PROTOCOL_VERSIONS, init_future


        # tools/list == the 14 tools (12 consolidated + set_theme + show_map)
        tl = _parse_sse(_mcp(client, sid, {
            "jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}).text)
        tools = {t["name"] for t in tl.get("result", {}).get("tools", [])}
        assert tools == EXPECTED_TOOLS, f"got {sorted(tools)}"

        # ── MCP Apps (ext-apps / SEP-1865; mcp_apps.py) ─────────────────────
        # UI-enabled tools carry _meta.ui.resourceUri (nested form — the flat
        # "ui/resourceUri" key is deprecated and must NOT be used).
        _tools_by_name = {t["name"]: t for t in tl["result"]["tools"]}
        for _name in UI_ENABLED_TOOLS:
            _tmeta = _tools_by_name[_name].get("_meta") or {}
            assert (_tmeta.get("ui") or {}).get("resourceUri") == UI_TEMPLATE_URI, (
                f"{_name} missing _meta.ui.resourceUri: {_tmeta}")
            assert "ui/resourceUri" not in _tmeta, f"deprecated flat key on {_name}"
        # Non-UI tools stay clean.
        assert "ui" not in ((_tools_by_name["zoom"].get("_meta") or {})), \
            _tools_by_name["zoom"].get("_meta")

        # The ui:// template reads back with the exact mimeType, valid HTML5,
        # and the _meta.ui.csp.frameDomains stamping (mcp_apps.py).
        ui_read = _parse_sse(_mcp(client, sid, {
            "jsonrpc": "2.0", "id": 40, "method": "resources/read",
            "params": {"uri": UI_TEMPLATE_URI}}).text)
        ui_contents = ui_read.get("result", {}).get("contents", [])
        assert ui_contents, ui_read
        assert ui_contents[0]["mimeType"] == UI_MIME_TYPE, ui_contents[0]
        assert ui_contents[0]["text"].lstrip().startswith("<!DOCTYPE html>")
        # _meta.ui.csp.frameDomains unlocks the nested live-map iframe in the
        # host sandbox (frame-src) — the Option-A gate (mcp_apps.py).
        _ui_meta = (ui_contents[0].get("_meta") or {}).get("ui") or {}
        _frame_domains = (_ui_meta.get("csp") or {}).get("frameDomains") or []
        assert _frame_domains and all(
            d.startswith("http") for d in _frame_domains), ui_contents[0]

        # ── 2026 draft-revision pre-adoption (mcp_compat.py) ────────────────
        # Additive result fields old clients ignore, new revision requires:
        # SEP-2322 resultType, SEP-2549 ttlMs/cacheScope on cacheable results,
        # SEP-2575 serverInfo in _meta. tools/list is static → public/1h.
        tl_result = tl["result"]
        assert tl_result.get("resultType") == "complete", tl_result.keys()
        assert tl_result.get("ttlMs") == 3_600_000, tl_result.get("ttlMs")
        assert tl_result.get("cacheScope") == "public", tl_result.get("cacheScope")
        _si = (tl_result.get("_meta") or {}).get(
            "io.modelcontextprotocol/serverInfo") or {}
        assert _si.get("name") == "ESIP Map Control", tl_result.get("_meta")
        # Deterministic tools/list ordering (draft minor #3): a second call
        # returns the identical sequence (prompt-cache friendliness).
        tl2 = _parse_sse(_mcp(client, sid, {
            "jsonrpc": "2.0", "id": 24, "method": "tools/list", "params": {}}).text)
        assert ([t["name"] for t in tl["result"]["tools"]]
                == [t["name"] for t in tl2["result"]["tools"]])

        # create_map — result carries the map_id AND a resource_link block
        # pointing at the canonical map:// resource (§5c/§6).
        cm = _parse_sse(_mcp(client, sid, {
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "create_map", "arguments": {}}}).text)
        cm_result = cm.get("result", {})
        map_id = _structured(cm_result).get("map_id")
        assert map_id, cm
        cm_links = [c for c in cm_result.get("content", [])
                    if c.get("type") == "resource_link"]
        assert cm_links and cm_links[0]["uri"] == f"map://{map_id}", cm_result

        # add_geojson (valid polygon)
        poly = json.dumps({
            "type": "Feature", "properties": {},
            "geometry": {"type": "Polygon", "coordinates": [[
                [-97.76, 30.26], [-97.73, 30.26], [-97.73, 30.28],
                [-97.76, 30.28], [-97.76, 30.26]]]}})
        ag = _parse_sse(_mcp(client, sid, {
            "jsonrpc": "2.0", "id": 4, "method": "tools/call",
            "params": {"name": "add_geojson",
                       "arguments": {"map_id": map_id, "geojson": poly,
                                     "name": "Test"}}}).text)
        ag_result = ag.get("result", {})
        asset_id = _structured(ag_result).get("asset_id")
        assert asset_id and not ag_result.get("isError"), ag
        # add_geojson links the created asset's geo+json resource
        ag_links = [c for c in ag_result.get("content", [])
                    if c.get("type") == "resource_link"]
        assert any(
            c["uri"] == f"map://{map_id}/assets/{asset_id}" for c in ag_links
        ), ag_result

        # REST sees the MCP-created asset (shared state)
        rest_assets = client.get(f"/api/maps/{map_id}/assets").json()
        assert any(a.get("asset_id") == asset_id for a in rest_assets)

        # show_map (MCP Apps): round-trips an existing map — url + resource
        # link; unknown map → Tool Execution Error (isError), not a crash.
        sm = _parse_sse(_mcp(client, sid, {
            "jsonrpc": "2.0", "id": 41, "method": "tools/call",
            "params": {"name": "show_map", "arguments": {"map_id": map_id}}}).text)
        sm_result = sm.get("result", {})
        sm_doc = _structured(sm_result)
        assert sm_doc.get("map_id") == map_id and not sm_result.get("isError"), sm
        assert sm_doc.get("url", "").endswith(f"/map/{map_id}"), sm_doc
        sm_bad = _parse_sse(_mcp(client, sid, {
            "jsonrpc": "2.0", "id": 42, "method": "tools/call",
            "params": {"name": "show_map",
                       "arguments": {"map_id": "no-such-map"}}}).text)
        assert sm_bad.get("result", {}).get("isError") is True, sm_bad

        # ── §6 Resources ────────────────────────────────────────────────────

        # resources/templates/list == the full map:// taxonomy
        rt = _parse_sse(_mcp(client, sid, {
            "jsonrpc": "2.0", "id": 10, "method": "resources/templates/list",
            "params": {}}).text)
        templates = {t["uriTemplate"]
                     for t in rt.get("result", {}).get("resourceTemplates", [])}
        assert templates == EXPECTED_RESOURCE_TEMPLATES, f"got {sorted(templates)}"

        # resources/list contains the concrete map://maps index
        rl = _parse_sse(_mcp(client, sid, {
            "jsonrpc": "2.0", "id": 11, "method": "resources/list",
            "params": {}}).text)
        concrete = {r["uri"] for r in rl.get("result", {}).get("resources", [])}
        assert "map://maps" in concrete, concrete
        # MCP Apps: ui:// templates are hidden from resources/list (discovery
        # is via tool _meta; keeps the map:// taxonomy clean).
        assert not any(u.startswith("ui://") for u in concrete), concrete

        # resources/read map://maps — JSON index that includes our map
        rm = _parse_sse(_mcp(client, sid, {
            "jsonrpc": "2.0", "id": 12, "method": "resources/read",
            "params": {"uri": "map://maps"}}).text)
        rm_contents = rm.get("result", {}).get("contents", [])
        assert rm_contents and rm_contents[0]["mimeType"] == "application/json", rm
        # SEP-2549: live map state is private / short-TTL (mcp_compat.py).
        assert rm["result"].get("cacheScope") == "private", rm["result"]
        assert rm["result"].get("ttlMs") == 5_000, rm["result"]
        maps_doc = json.loads(rm_contents[0]["text"])
        assert any(m["map_id"] == map_id for m in maps_doc["maps"]), maps_doc

        # resources/read the assets index — sees the tool-created asset and
        # carries its canonical per-asset resource_uri
        ra = _parse_sse(_mcp(client, sid, {
            "jsonrpc": "2.0", "id": 13, "method": "resources/read",
            "params": {"uri": f"map://{map_id}/assets"}}).text)
        ra_contents = ra.get("result", {}).get("contents", [])
        assert ra_contents and ra_contents[0]["mimeType"] == "application/json", ra
        assets_doc = json.loads(ra_contents[0]["text"])
        entry = next(a for a in assets_doc["assets"] if a["asset_id"] == asset_id)
        assert entry["resource_uri"] == f"map://{map_id}/assets/{asset_id}"

        # resources/read the asset itself — application/geo+json geometry
        rg = _parse_sse(_mcp(client, sid, {
            "jsonrpc": "2.0", "id": 14, "method": "resources/read",
            "params": {"uri": f"map://{map_id}/assets/{asset_id}"}}).text)
        rg_contents = rg.get("result", {}).get("contents", [])
        assert rg_contents and rg_contents[0]["mimeType"] == "application/geo+json", rg
        gj = json.loads(rg_contents[0]["text"])
        assert gj["geometry"]["type"] == "Polygon", gj

        # resources/read viewport — JSON snapshot
        rv = _parse_sse(_mcp(client, sid, {
            "jsonrpc": "2.0", "id": 15, "method": "resources/read",
            "params": {"uri": f"map://{map_id}/viewport"}}).text)
        rv_contents = rv.get("result", {}).get("contents", [])
        assert rv_contents and rv_contents[0]["mimeType"] == "application/json", rv
        assert json.loads(rv_contents[0]["text"])["map_id"] == map_id

        # Binary read: seed a PNG where screenshot_service would save one, then
        # read it through map://{map_id}/screenshots/{id} — base64 blob, image/png.
        import base64 as _b64
        _png = (b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
        _shot_id = "testshot-0001"
        _file_dir = os.environ["MAPCONTROL_FILE_DIR"]
        os.makedirs(_file_dir, exist_ok=True)
        with open(os.path.join(_file_dir, f"screenshot-{_shot_id}.png"), "wb") as fh:
            fh.write(_png)
        rs = _parse_sse(_mcp(client, sid, {
            "jsonrpc": "2.0", "id": 16, "method": "resources/read",
            "params": {"uri": f"map://{map_id}/screenshots/{_shot_id}"}}).text)
        rs_contents = rs.get("result", {}).get("contents", [])
        assert rs_contents and rs_contents[0]["mimeType"] == "image/png", rs
        assert _b64.b64decode(rs_contents[0]["blob"]) == _png

        # Unknown map → resource read error (protocol survives; no crash).
        # SEP-2164 (2026 draft revision): resource-not-found MUST be -32602
        # (Invalid Params), not the old -32002 / generic 0 — mcp_compat.py.
        bad_read = _parse_sse(_mcp(client, sid, {
            "jsonrpc": "2.0", "id": 17, "method": "resources/read",
            "params": {"uri": "map://no-such-map/assets"}}).text)
        assert "error" in bad_read, bad_read
        assert bad_read["error"].get("code") == -32602, bad_read["error"]

        # Unknown asset on a real map → error too (same code)
        bad_asset = _parse_sse(_mcp(client, sid, {
            "jsonrpc": "2.0", "id": 18, "method": "resources/read",
            "params": {"uri": f"map://{map_id}/assets/nope"}}).text)
        assert "error" in bad_asset, bad_asset
        assert bad_asset["error"].get("code") == -32602, bad_asset["error"]

        # set_theme: dark persists at the map level and is served to viewers
        st = _parse_sse(_mcp(client, sid, {
            "jsonrpc": "2.0", "id": 6, "method": "tools/call",
            "params": {"name": "set_theme",
                       "arguments": {"map_id": map_id, "theme": "dark"}}}).text)
        st_result = st.get("result", {})
        assert not st_result.get("isError"), st
        assert _structured(st_result).get("theme") == "dark", st
        themed_page = client.get(f"/map/{map_id}")
        assert 'data-theme="dark"' in themed_page.text
        # theme→basemap coupling: a dark map with NO explicit basemap serves
        # the keyless dark default (carto_dark) instead of satellite, and the
        # canvas surround is bound to the theme tokens (visibly dark).
        assert '"basemap": "carto_dark"' in themed_page.text
        assert "background: var(--eo-bg)" in themed_page.text
        # ?theme= query still wins as a per-request override
        light_page = client.get(f"/map/{map_id}?theme=light")
        assert 'data-theme="light"' in light_page.text

        # Sticky per-user prefs: set_theme('dark') above was recorded for the
        # (anonymous, in this unauthenticated suite) caller, so a NEW
        # create_map with no args now defaults to dark.
        cm2 = _parse_sse(_mcp(client, sid, {
            "jsonrpc": "2.0", "id": 7, "method": "tools/call",
            "params": {"name": "create_map", "arguments": {}}}).text)
        cm2_s = _structured(cm2.get("result", {}))
        assert cm2_s.get("theme") == "dark", cm2
        assert cm2_s.get("owner") == "anonymous", cm2

        # Explicit set_basemap pins the MAP-level basemap and beats the
        # dark default on subsequent loads of that map.
        sb = _parse_sse(_mcp(client, sid, {
            "jsonrpc": "2.0", "id": 8, "method": "tools/call",
            "params": {"name": "set_basemap",
                       "arguments": {"map_id": map_id, "basemap": "satellite"}}}).text)
        assert not sb.get("result", {}).get("isError"), sb
        pinned_page = client.get(f"/map/{map_id}")
        assert '"basemap": "satellite"' in pinned_page.text

        # Explicit create_map(theme=...) still wins over the sticky pref
        # (and resets it going forward).
        cm3 = _parse_sse(_mcp(client, sid, {
            "jsonrpc": "2.0", "id": 9, "method": "tools/call",
            "params": {"name": "create_map", "arguments": {"theme": "auto"}}}).text)
        assert _structured(cm3.get("result", {})).get("theme") == "auto", cm3

        # ── Camera persistence (screenshot globe-bug fix) ────────────────────
        # zoom used to be broadcast-only: live viewers moved but nothing was
        # stored, so a brand-new session (critically the headless screenshot
        # page) booted at the world default and captured the globe. Now the
        # requested view is materialized at MAP level and inherited by fresh
        # sessions + the served page.
        zm = _parse_sse(_mcp(client, sid, {
            "jsonrpc": "2.0", "id": 20, "method": "tools/call",
            "params": {"name": "zoom",
                       "arguments": {"map_id": map_id, "target": "point",
                                     "lon": 19.0402, "lat": 47.4979,
                                     "zoom": 12}}}).text)
        assert not zm.get("result", {}).get("isError"), zm

        # get_viewport with NO session inherits the persisted camera
        gv = _parse_sse(_mcp(client, sid, {
            "jsonrpc": "2.0", "id": 21, "method": "tools/call",
            "params": {"name": "get_viewport",
                       "arguments": {"map_id": map_id}}}).text)
        vp = _structured(gv.get("result", {})).get("viewport") or {}
        assert vp.get("center") == [19.0402, 47.4979], gv
        assert vp.get("zoom") == 12, gv

        # The served page renders the initial camera SERVER-SIDE, so the very
        # first tiles a fresh/headless viewer requests are the right ones.
        boot_page = client.get(f"/map/{map_id}")
        assert '"center": [19.0402, 47.4979]' in boot_page.text, "initial center not injected"
        assert '"zoom": 12.0' in boot_page.text, "initial zoom not injected"

        # bbox zoom persists too and is rendered as initial bounds
        zb = _parse_sse(_mcp(client, sid, {
            "jsonrpc": "2.0", "id": 22, "method": "tools/call",
            "params": {"name": "zoom",
                       "arguments": {"map_id": map_id, "target": "bbox",
                                     "bbox": [18.9, 47.4, 19.2, 47.6]}}}).text)
        assert not zb.get("result", {}).get("isError"), zb
        gv2 = _parse_sse(_mcp(client, sid, {
            "jsonrpc": "2.0", "id": 23, "method": "tools/call",
            "params": {"name": "get_viewport",
                       "arguments": {"map_id": map_id}}}).text)
        vp2 = _structured(gv2.get("result", {})).get("viewport") or {}
        assert vp2.get("bbox") == [18.9, 47.4, 19.2, 47.6], gv2
        bounds_page = client.get(f"/map/{map_id}")
        assert '"bounds": [18.9, 47.4, 19.2, 47.6]' in bounds_page.text

        # malformed GeoJSON -> Tool Execution Error, not a JSON-RPC error.
        # Even error-shaped tool results are ordinary complete results
        # (SEP-2322): resultType must still be "complete".
        bad = _parse_sse(_mcp(client, sid, {
            "jsonrpc": "2.0", "id": 5, "method": "tools/call",
            "params": {"name": "add_geojson",
                       "arguments": {"map_id": map_id,
                                     "geojson": "{not valid json"}}}).text)
        assert "result" in bad and bad["result"].get("isError") is True, bad
        assert bad["result"].get("resultType") == "complete", bad["result"]

        # /map/{id} still serves HTML
        mp = client.get(f"/map/{map_id}")
        assert mp.status_code == 200
        assert "text/html" in mp.headers.get("content-type", "")


def test_inline_screenshot_always_renders():
    """Oversized screenshots must still produce an inline ImageContent block.

    The inline guardrail (_MAX_INLINE_IMAGE_BYTES) used to drop to link-only
    for large captures — and a good satellite view over a city easily exceeds
    it as PNG, so exactly the *best* screenshots stopped rendering in Claude.
    Now: small PNG → inlined verbatim (image/png, lossless); oversized PNG →
    re-encoded to JPEG (downscaled if needed) under the cap (image/jpeg)."""
    import base64 as _b64
    import io

    from PIL import Image

    from mapcontrol_server.mcp_tools import (
        _MAX_INLINE_IMAGE_BYTES,
        _inline_image_content,
    )

    # Small PNG → verbatim, lossless
    small = io.BytesIO()
    Image.new("RGB", (64, 64), (30, 60, 90)).save(small, format="PNG")
    content, note = _inline_image_content(small.getvalue())
    assert content is not None and content.mimeType == "image/png"
    assert note is None
    assert _b64.b64decode(content.data) == small.getvalue()

    # Oversized PNG (noise compresses badly → guaranteed > cap) → inline JPEG
    import random
    rng = random.Random(42)
    big_img = Image.new("RGB", (1280, 720))
    big_img.putdata(
        [(rng.randrange(256), rng.randrange(256), rng.randrange(256))
         for _ in range(1280 * 720)]
    )
    big = io.BytesIO()
    big_img.save(big, format="PNG")
    assert len(big.getvalue()) > _MAX_INLINE_IMAGE_BYTES, len(big.getvalue())
    content2, note2 = _inline_image_content(big.getvalue())
    assert content2 is not None, note2
    assert content2.mimeType == "image/jpeg"
    jpeg_bytes = _b64.b64decode(content2.data)
    assert len(jpeg_bytes) <= _MAX_INLINE_IMAGE_BYTES
    assert jpeg_bytes[:2] == b"\xff\xd8"  # JPEG SOI marker
    assert note2 and "JPEG" in note2


def test_served_map_no_root_path():
    """Phase 1 Tier B (reverse-proxy readiness): with NO root_path (the default,
    served at root — ADR-0001 dual-deployability), the inline map HTML must emit
    UN-prefixed WebSocket + /static URLs, exactly as before. Guards the local /
    direct-port / internal-M2M path against regressions from the prefix work."""
    html = _render_map_html(root_path="")
    assert 'const ROOT_PATH = "";' in html
    # WS built from window.location.host with an empty prefix
    assert "window.location.host + ROOT_PATH + '/ws/'" in html
    # esip-contract.js is ALWAYS injected; at root it has no prefix
    assert '"/static/esip-contract.js"' in html
    # base_url unprefixed → REST/screenshot URLs resolve at root
    assert 'const BASE_URL = "http://testserver";' in html



def test_served_map_with_root_path():
    """Phase 1 Tier B: behind a single-origin reverse proxy the server runs under
    a sub-path (MAPCONTROL_ROOT_PATH=/service/map → uvicorn --root-path, surfaced
    on request.scope['root_path']). The inline map HTML must then prefix every
    URL that bypasses request.base_url — the WebSocket URLs and the root-absolute
    /static asset tags — so the browser reaches them through the proxy. base_url-
    derived REST/screenshot URLs are auto-prefixed by Starlette and need no help.

    The synthetic scope carries the ASGI root_path the proxy/uvicorn would set,
    so this exercises the exact production prefixing without a real proxy."""
    prefix = "/service/map"
    html = _render_map_html(root_path=prefix)
    assert f'const ROOT_PATH = "{prefix}";' in html
    assert "window.location.host + ROOT_PATH + '/ws/'" in html
    # static asset tags carry the prefix
    assert f'"{prefix}/static/esip-contract.js"' in html
    # base_url (and thus every BASE_URL-derived fetch) is auto-prefixed
    assert f'const BASE_URL = "http://testserver{prefix}";' in html




if __name__ == "__main__":
    test_mcp_phase0()
    test_inline_screenshot_always_renders()
    test_served_map_no_root_path()
    test_served_map_with_root_path()
    print("RESULT: ALL PASSED", flush=True)
    # Force an immediate, clean exit. The MCP Streamable-HTTP session manager
    # (and the TestClient's anyio portal / aiosqlite) leave non-daemon worker
    # threads alive after the assertions pass, so a normal `return` would block
    # interpreter shutdown indefinitely — which hangs `python tests/test_mcp.py`
    # in CI forever. We've already validated everything above; skip the
    # uncooperative thread teardown. (pytest does NOT enter this block.)
    os._exit(0)


