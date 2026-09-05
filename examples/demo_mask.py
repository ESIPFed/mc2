#!/usr/bin/env python3
"""Demo: style.mask — the "spotlight" polygon.

A polygon with an outline, a see-through interior (just the map), and a
darkened exterior. Exercised end-to-end against a running MapControl
server via the public REST events API (no SDK required):

  Scene 1  add_polygon + style.mask=True          → spotlight on Lake Balaton
  Scene 2  update_style retune / clear / restore  → opacity + color knobs, mask=False
  Scene 3  MultiPolygon mask                      → two islands, one mask
  Scene 4  set_basemap + set_visibility           → mask survives a basemap switch,
                                                    hides/shows with its asset

Usage:
    # In one terminal (from server/):
    uv run uvicorn mapcontrol_server.main:app --port 8000

    # In another:
    python3 demo_mask.py [--server http://localhost:8000] [--fast] [--no-browser]
"""

import argparse
import json
import sys
import time
import webbrowser

import requests

parser = argparse.ArgumentParser()
parser.add_argument("--server", default="http://localhost:8000")
parser.add_argument("--fast", action="store_true", help="minimal pauses")
parser.add_argument("--no-browser", action="store_true", help="don't open the map in a browser")
args = parser.parse_args()

SERVER = args.server.rstrip("/")
PAUSE = 1.0 if args.fast else 4.0


def wait(seconds=None):
    time.sleep(seconds if seconds is not None else PAUSE)


def banner(text):
    print(f"\n{'─' * 62}\n  {text}\n{'─' * 62}")


# ─── Preflight ────────────────────────────────────────────────────────────────
try:
    r = requests.get(f"{SERVER}/health", timeout=5)
    r.raise_for_status()
except Exception as e:
    print(f"❌ MapControl server not reachable at {SERVER} ({e})")
    print("   Start it with:  cd server && uv run uvicorn mapcontrol_server.main:app --port 8000")
    sys.exit(1)
print(f"✅ Server up at {SERVER}")

# ─── Create map + session ────────────────────────────────────────────────────
resp = requests.post(f"{SERVER}/api/maps", json={}, timeout=10)
resp.raise_for_status()
map_id = resp.json()["map_id"]

resp = requests.post(f"{SERVER}/api/maps/{map_id}/sessions", json={}, timeout=10)
resp.raise_for_status()
session_id = resp.json()["user_session_id"]

map_url = f"{SERVER}/map/{map_id}?user_session={session_id}"
print(f"🗺️  Map: {map_url}")
if not args.no_browser:
    webbrowser.open(map_url)
    print("   (waiting for the page to load…)")
    wait(4)


def send(event_type, data):
    resp = requests.post(
        f"{SERVER}/api/maps/{map_id}/events",
        json={"type": event_type, "data": data, "user_session_id": session_id},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


# ─── Geometry ────────────────────────────────────────────────────────────────
# Lake Balaton, Hungary — a rough outline of the lake (lon, lat).
BALATON = {
    "type": "Feature",
    "properties": {"name": "Lake Balaton"},
    "geometry": {"type": "Polygon", "coordinates": [[
        [17.24, 46.77], [17.42, 46.70], [17.62, 46.72], [17.80, 46.80],
        [17.95, 46.87], [18.10, 46.93], [18.17, 47.02], [18.06, 47.05],
        [17.90, 46.98], [17.72, 46.93], [17.55, 46.88], [17.38, 46.86],
        [17.26, 46.83], [17.24, 46.77],
    ]]},
}

# Two islands in the Aegean — a MultiPolygon punches two holes in one mask.
ISLANDS = {
    "type": "Feature",
    "properties": {"name": "Paros & Naxos"},
    "geometry": {"type": "MultiPolygon", "coordinates": [
        # Paros
        [[[25.08, 37.00], [25.20, 36.98], [25.27, 37.04], [25.24, 37.14],
          [25.14, 37.16], [25.07, 37.10], [25.08, 37.00]]],
        # Naxos
        [[[25.33, 36.96], [25.45, 36.93], [25.57, 37.03], [25.54, 37.14],
          [25.44, 37.20], [25.34, 37.12], [25.33, 36.96]]],
    ]},
}


# ═════════════════════════════════════════════════════════════════════════════
banner("Scene 1 — add_polygon + style.mask=True  (the spotlight)")
send("set_basemap", {"basemap": "satellite"})
r = send("add_polygon", {
    "geojson": json.dumps(BALATON),
    "name": "Lake Balaton",
    # No fill_color → interior is the bare map. Outline + mask do the work.
    "style": {"stroke_color": "#ffffff", "stroke_width": 3, "mask": True},
})
balaton_id = r["asset_id"]
send("zoom_to_assets", {"asset_ids": [balaton_id]})
print(f"   asset_id={balaton_id}  → outline, clear interior, dimmed exterior")
wait()

# ═════════════════════════════════════════════════════════════════════════════
banner("Scene 2 — update_style: retune → clear → restore")
send("update_style", {
    "asset_id": balaton_id,
    "style": {"mask": {"color": "#0b1020", "opacity": 0.85}},
})
print("   mask → deep navy, opacity 0.85")
wait()

send("update_style", {"asset_id": balaton_id, "style": {"mask": False}})
print("   mask → False (cleared; plain outlined polygon remains)")
wait()

send("update_style", {
    "asset_id": balaton_id,
    "style": {"mask": {"color": "#000000", "opacity": 0.6}, "stroke_color": "#ffd93d"},
})
print("   mask → back on (black 0.6) with a yellow outline")
wait()

# ═════════════════════════════════════════════════════════════════════════════
banner("Scene 3 — MultiPolygon: two islands, one mask")
send("set_visibility", {"asset_id": balaton_id, "visible": False})
r = send("add_polygon", {
    "geojson": json.dumps(ISLANDS),
    "name": "Paros & Naxos",
    "style": {"stroke_color": "#4ecca3", "stroke_width": 2, "mask": {"opacity": 0.7},
              "label": True, "label_placement": "center", "label_color": "#ffffff"},
})
islands_id = r["asset_id"]
send("zoom_to_assets", {"asset_ids": [islands_id]})
print(f"   asset_id={islands_id}  → both islands lit, sea + neighbours dimmed")
wait()

# ═════════════════════════════════════════════════════════════════════════════
banner("Scene 4 — mask survives a basemap switch; toggles with its asset")
send("set_basemap", {"basemap": "osm"})
print("   basemap → osm (mask re-attached after setStyle)")
wait()

send("set_visibility", {"asset_id": islands_id, "visible": False})
print("   islands hidden → mask hidden too")
wait(PAUSE / 2)
send("set_visibility", {"asset_id": islands_id, "visible": True})
print("   islands shown → mask back")
wait()

send("set_basemap", {"basemap": "satellite"})
send("set_visibility", {"asset_id": balaton_id, "visible": True})
send("zoom_to_assets", {"asset_ids": [balaton_id, islands_id]})
print("   both masked assets visible (masks compound where they overlap)")

print(f"\n✅ Done. Keep playing with the map: {map_url}")

