"""Live viewer == stored state, in a real browser.

Drives the map page in headless Chromium while posting events over HTTP,
and compares what MapLibre actually draws (layer stack, paint values) with
GET /assets — live, after a reload (session snapshot), and for inserts that
follow a reload. Every check here failed before the layer-state fixes.
Skips when Playwright or its Chromium is not installed.
"""
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request

import pytest

sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_live_viewer_matches_stored_state(tmp_path):
    PORT = _free_port()
    BASE = f"http://127.0.0.1:{PORT}"
    env = dict(os.environ, MAPCONTROL_DB_PATH=str(tmp_path / "t.db"), MAPCONTROL_FILE_DIR=str(tmp_path / "f"))
    srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "mapcontrol_server.main:app", "--port", str(PORT)],
                           env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    def req(method, path, body=None):
        r = urllib.request.Request(BASE + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                   headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(r) as resp:
            t = resp.read()
            return json.loads(t) if t else None

    def ev(mid, t, d):
        out = req("POST", f"/api/maps/{mid}/events", {"type": t, "data": d})
        assert not out.get("error"), out
        return out

    POLY = json.dumps({"type": "Feature", "properties": {}, "geometry": {"type": "Polygon",
            "coordinates": [[[-97.7, 30.2], [-97.7, 30.3], [-97.6, 30.3], [-97.6, 30.2], [-97.7, 30.2]]]}})
    PT = json.dumps({"type": "Point", "coordinates": [-97.65, 30.25]})
    TILE = BASE + "/nope/{z}/{x}/{y}.png"
    fails = []
    def check(name, cond, detail=""):
        print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if not cond else ""))
        if not cond: fails.append(name)

    STATE_JS = """() => {
      const I = window.__esipInternals, map = I.map, reg = I.registry;
      const owner = {}; for (const [id, r] of Object.entries(reg)) for (const l of r.layerIds) owner[l] = id;
      const order = []; for (const l of map.getStyle().layers) { const o = owner[l.id]; if (o && !order.includes(o)) order.push(o); }
      const paint = {};
      for (const [id, r] of Object.entries(reg)) { paint[id] = {};
        for (const l of r.layerIds) { const L = map.getLayer(l); if (!L) continue;
          const k = {fill: ['fill-opacity','fill-color'], line: ['line-opacity','line-width','line-color'], raster: ['raster-opacity'], circle: ['circle-opacity','circle-color']}[L.type] || [];
          for (const p of k) paint[id][L.type + ':' + p] = map.getPaintProperty(l, p);
          paint[id][L.type + ':visibility'] = map.getLayoutProperty(l, 'visibility') || 'visible';
        } }
      return { order, paint, msgs: window.__msgs };
    }"""


    try:
        for _ in range(50):
            try: req("GET", "/api/maps"); break
            except Exception: time.sleep(0.2)
        mid = req("POST", "/api/maps")["map_id"]
        with sync_playwright() as pw:
            try:
                b = pw.chromium.launch(args=["--use-gl=swiftshader", "--enable-unsafe-swiftshader"])
            except Exception as exc:  # browsers not installed
                pytest.skip(f"Chromium unavailable: {exc}")
            page = b.new_page()
            page.add_init_script("window.__msgs=[]; window.addEventListener('message', e => { if (e.data && e.data.source==='mapcontrol' && e.data.type==='assetschanged') window.__msgs.push(e.data.data.type); });")
            def load():
                page.goto(f"{BASE}/map/{mid}?ui=controls")
                page.wait_for_function("window.__esipInternals && window.__esipInternals.map && window.__esipInternals.map.loaded()", timeout=30000)
                page.wait_for_timeout(1500)
            def state(): page.wait_for_timeout(600); return page.evaluate(STATE_JS)
            load()

            A = ev(mid, "add_polygon", {"geojson": POLY, "name": "A", "style": {"fill_color": "#ff000080", "stroke_color": "#00ff00"}})["asset_id"]
            B = ev(mid, "add_point", {"geojson": PT, "name": "B"})["asset_id"]
            R = ev(mid, "add_tile_layer", {"url": TILE, "name": "R", "opacity": 0.7})["asset_id"]
            C = ev(mid, "add_polygon", {"geojson": POLY, "name": "C"})["asset_id"]
            s = state()
            api = [a["asset_id"] for a in req("GET", f"/api/maps/{mid}/assets?include_geojson=false")]
            check("live order == API order after raster insert", s["order"] == api, f"{s['order']} vs {api}")
            check("raster below vectors", s["order"][0] == R, s["order"])
            check("A fill-opacity is the flat layer opacity (1)", s["paint"][A]["fill:fill-opacity"] == 1, s["paint"][A])
            fc = s["paint"][A]["fill:fill-color"]
            check("A fill colour is its stored colour (+hover lift)", isinstance(fc, list) and fc[-1] == "#ff000080", fc)
            check("tile raster-opacity 0.7", s["paint"][R]["raster:raster-opacity"] == 0.7, s["paint"][R])

            ev(mid, "set_opacity", {"asset_id": A, "opacity": 0.4})
            ev(mid, "update_style", {"asset_id": A, "style": {"stroke_width": 5}})
            s = state()
            p = s["paint"][A]
            check("opacity survives a later partial update_style", p["fill:fill-opacity"] == 0.4 and p["line:line-opacity"] == 0.4, p)
            check("stroke width applied, colour kept", p["line:line-width"] == 5 and p["line:line-color"] == "#00ff00", p)
            st = req("GET", f"/api/maps/{mid}/assets/{A}")["style"]
            check("API style == drawn style", st["opacity"] == 0.4 and st["stroke_width"] == 5 and st["fill_color"] == "#ff000080", st)

            ev(mid, "reorder_assets", {"asset_ids": [A, C, B, R]})
            s = state()
            check("reorder: live bottom→top R,B,C,A", s["order"] == [R, B, C, A], s["order"])
            check("reorder posts assetschanged to parent", "reorder_assets" in s["msgs"], s["msgs"])
            ev(mid, "move_layer", {"asset_id": B, "position": "top"})
            s = state()
            api = [a["asset_id"] for a in req("GET", f"/api/maps/{mid}/assets?include_geojson=false")]
            check("move_layer: live == API", s["order"] == api == [R, C, A, B], f"{s['order']} vs {api}")

            ev(mid, "set_visibility", {"asset_id": C, "visible": False})
            load()   # ── reload: session snapshot ──
            s = state()
            api = [a["asset_id"] for a in req("GET", f"/api/maps/{mid}/assets?include_geojson=false")]
            check("after reload: order == API", s["order"] == api, f"{s['order']} vs {api}")
            check("after reload: tile layer restored at 0.7", R in s["paint"] and s["paint"][R].get("raster:raster-opacity") == 0.7, s["paint"].get(R))
            check("after reload: A opacity 0.4", s["paint"][A]["fill:fill-opacity"] == 0.4, s["paint"][A])
            check("after reload: C hidden", s["paint"][C]["fill:visibility"] == "none", s["paint"][C])
            check("snapshot posts assetschanged to parent", "snapshot" in s["msgs"], s["msgs"])

            R2 = ev(mid, "add_tile_layer", {"url": TILE, "name": "R2"})["asset_id"]
            s = state()
            api = [a["asset_id"] for a in req("GET", f"/api/maps/{mid}/assets?include_geojson=false")]
            check("raster added after reload lands where the API says", s["order"] == api, f"{s['order']} vs {api}")
            b.close()
    finally:
        srv.terminate()
    assert not fails, fails
