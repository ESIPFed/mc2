"""Frame stacks + video overlays in a real browser.

Drives the map page in headless Chromium: frames advance and pause, a VP9
(alpha) video plays and pauses, hide/show, reload restore, delete, the
still-frame mode headless screenshots use, and stable API screenshots.
Skips when Playwright or its Chromium is not installed.
"""
import hashlib
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright

FIXTURE_WEBM = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "alpha-vp9.webm")


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _blob_frames(tmp_path, n=3):
    yy, xx = np.mgrid[0:60, 0:80]
    paths = []
    for i in range(n):
        data = np.exp(-(((xx - 15 - 25 * i) / 9.0) ** 2 + ((yy - 30) / 9.0) ** 2)) * 3
        path = str(tmp_path / f"blob-{i}.tif")
        with rasterio.open(path, "w", driver="GTiff", width=80, height=60, count=1, dtype="float32",
                           crs="EPSG:4326", transform=from_origin(0.0, 20.0, 0.5, 0.5)) as ds:
            ds.write(data.astype("float32"), 1)
        paths.append(path)
    return paths


def test_frames_and_video_play_pause_restore(tmp_path):
    PORT = _free_port()
    BASE = f"http://127.0.0.1:{PORT}"
    env = dict(os.environ, MAPCONTROL_DB_PATH=str(tmp_path / "t.db"),
               MAPCONTROL_FILE_DIR=str(tmp_path / "f"), MAPCONTROL_PORT=str(PORT))
    srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "mapcontrol_server.main:app", "--port", str(PORT)],
                           env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    def req(method, path, body=None, raw=False):
        r = urllib.request.Request(BASE + path, method=method,
                                   data=json.dumps(body).encode() if body is not None else None,
                                   headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(r, timeout=120) as resp:
            t = resp.read()
            return t if raw else (json.loads(t) if t else None)

    def ev(mid, t, d):
        out = req("POST", f"/api/maps/{mid}/events", {"type": t, "data": d})
        assert not out.get("error"), out
        return out

    fails = []

    def check(name, cond, detail=""):
        print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if not cond else ""))
        if not cond:
            fails.append(name)

    try:
        for _ in range(50):
            try:
                req("GET", "/api/maps")
                break
            except Exception:
                time.sleep(0.2)
        mid = req("POST", "/api/maps")["map_id"]
        F = ev(mid, "add_geotiff_frames", {"urls": _blob_frames(tmp_path), "vmin": 0, "vmax": 3, "fps": 4,
                                           "labels": ["a", "b", "c"], "name": "Blob"})["asset_id"]
        V = ev(mid, "add_video", {"urls": [FIXTURE_WEBM], "bounds": [150, -25, 250, 25], "name": "Clip"})["asset_id"]
        with sync_playwright() as pw:
            try:
                b = pw.chromium.launch(args=["--use-gl=swiftshader", "--enable-unsafe-swiftshader",
                                             "--autoplay-policy=no-user-gesture-required"])
            except Exception as exc:  # browsers not installed
                pytest.skip(f"Chromium unavailable: {exc}")
            page = b.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))

            def load(q="?ui=controls", ids=None):
                ids = ids or [F, V]
                page.goto(f"{BASE}/map/{mid}{q}")
                # map.loaded() stays false while a video plays (MapLibre keeps rendering).
                page.wait_for_function("window.__esipInternals && window.__esipInternals.map"
                                       " && window.__esipInternals.map.isStyleLoaded()", timeout=30000)
                page.wait_for_function(
                    "ids => { const s = window.__esipInternals.animations.state();"
                    " return ids.every(id => s[id] && (s[id].kind === 'frames' ? s[id].loaded : s[id].readyState >= 2)); }",
                    arg=ids, timeout=30000)
                page.wait_for_timeout(500)

            def st():
                return page.evaluate("() => window.__esipInternals.animations.state()")

            def rows():
                return page.evaluate("() => Array.from(document.querySelectorAll('.anim-row'))"
                                     ".map(r => ({id: r.dataset.assetId, text: r.innerText}))")

            load()
            seen = set()
            for _ in range(8):
                seen.add(st()[F]["frame"])
                page.wait_for_timeout(150)
            check("frames advance", seen == {0, 1, 2}, seen)
            t0 = st()[V]["currentTime"]
            page.wait_for_timeout(600)
            check("video plays", st()[V]["currentTime"] != t0 and st()[V]["paused"] is False, st()[V])
            check("video placed across the antimeridian", page.evaluate(
                f"() => window.__esipInternals.map.getSource('src-{V}').coordinates[0][0]") == -210)
            r = rows()
            check("a control row per animation, frames show i/N · label",
                  {x["id"] for x in r} == {F, V} and any("/3 · " in x["text"] for x in r), r)

            page.click(f".anim-row[data-asset-id='{F}'] button")
            page.click(f".anim-row[data-asset-id='{V}'] button")
            f0, t1 = st()[F]["frame"], st()[V]["currentTime"]
            page.wait_for_timeout(700)
            s = st()
            check("pause holds both", s[F]["frame"] == f0 and not s[F]["running"]
                  and s[V]["paused"] and s[V]["currentTime"] == t1, s)

            ev(mid, "set_visibility", {"asset_id": V, "visible": False})
            page.wait_for_timeout(400)
            check("hidden layer drops its control", V not in [x["id"] for x in rows()], rows())

            load()   # reload: session snapshot restores both (V hidden)
            s = st()
            check("restored and autoplaying", set(s) == {F, V} and s[F]["running"], s)
            check("hidden video stays paused after reload", s[V]["paused"] is True, s[V])
            check("restore uses image/video sources", page.evaluate(
                f"() => [window.__esipInternals.map.getSource('src-{F}').type,"
                f" window.__esipInternals.map.getSource('src-{V}').type]") == ["image", "video"])

            ev(mid, "delete_asset", {"asset_id": F})
            page.wait_for_timeout(400)
            check("delete removes playback and control", F not in st() and F not in [x["id"] for x in rows()])

            ev(mid, "set_visibility", {"asset_id": V, "visible": True})
            load("?ui=controls&animate=0", ids=[V])
            s = st()
            check("?animate=0 holds the first frame, no control",
                  s[V]["paused"] is True and s[V]["currentTime"] == 0 and rows() == [], (s, rows()))
            check("no page errors", not errors, errors)
            b.close()

        sid = req("POST", f"/api/maps/{mid}/sessions")["user_session_id"]
        shots = []
        for _ in range(2):
            out = req("POST", f"/api/maps/{mid}/sessions/{sid}/screenshot?width=640&height=400")
            shots.append(hashlib.sha256(req("GET", out["url"], raw=True)).hexdigest())
            time.sleep(1.0)
        check("API screenshots of a playing video are identical", shots[0] == shots[1], shots)
    finally:
        srv.terminate()
    assert not fails, fails
