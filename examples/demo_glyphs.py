#!/usr/bin/env python3
"""Demo: the new glyphs — labels (001/002), arcs (003), gradients + hover (005).

Exercises the July 2026 glyph additions end-to-end against a running
MapControl server, via the public REST events API (no SDK required):

  Scene 1 (sketch 001)  add_point + style.label            → named marker
  Scene 2 (sketch 002)  polygon + label_placement          → text ON the perimeter
  Scene 3 (sketch 003)  add_arc + endpoint names           → great-circle comparison
  Scene 4 (sketch 005)  color_by gradient + hover          → spread-over-time bands
  Scene 5               update_style label retrofit        → label an existing asset

Usage:
    # In one terminal (from server/):
    uv run uvicorn mapcontrol_server.main:app --port 8000

    # In another:
    python3 demo_glyphs.py [--server http://localhost:8000] [--fast]

The script opens the map in your browser and walks through the scenes with
pauses so you can watch. Hover over the gradient rings in Scene 4 to see the
feature-state highlight.
"""

import argparse
import json
import math
import sys
import time
import webbrowser

import requests

parser = argparse.ArgumentParser()
parser.add_argument("--server", default="http://localhost:8000")
parser.add_argument("--fast", action="store_true", help="minimal pauses")
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
    body = resp.json()
    if body.get("error"):
        print(f"   ⚠️  {event_type} error: {body['error']}")
    return body


# ═══ Scene 1 — sketch 001: labeled point ═════════════════════════════════════
banner("Scene 1 (001): Point with its name over it — BUDAPEST")
send("zoom_to_point", {"lon": 19.0402, "lat": 47.4979, "zoom": 11})
wait(2)
send("add_point", {
    "geojson": json.dumps({
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [19.0402, 47.4979]},
        "properties": {"name": "BUDAPEST"},
    }),
    "name": "Budapest",
    "style": {
        "fill_color": "#e94560",
        "stroke_color": "#ffffff",
        "stroke_width": 2,
        "label": True,               # ← the new bit: name rendered on the map
        "label_color": "#e94560",
        "label_size": 16,
    },
})
print("   red dot + 'BUDAPEST' text above it")
wait()

# ═══ Scene 2 — sketch 002: text ON the polygon perimeter ═════════════════════
banner("Scene 2 (002): Polygon with text along its PERIMETER")
send("zoom_to_bbox", {"bbox": [18.85, 47.40, 19.35, 47.62]})
wait(2)
# A rough Budapest-ish boundary polygon (hand-drawn; a real flow would geocode)
budapest_poly = {
    "type": "Feature",
    "properties": {"name": "BUDAPEST CITY LIMITS"},
    "geometry": {"type": "Polygon", "coordinates": [[
        [18.93, 47.55], [19.00, 47.60], [19.12, 47.605], [19.22, 47.57],
        [19.28, 47.50], [19.24, 47.44], [19.14, 47.41], [19.02, 47.42],
        [18.94, 47.47], [18.93, 47.55],
    ]]},
}
send("add_polygon", {
    "geojson": json.dumps(budapest_poly),
    "name": "Budapest City Limits",
    "style": {
        "fill_color": "#e94560",
        "stroke_color": "#e94560",
        "stroke_width": 3,
        "label": True,
        "label_placement": "perimeter",   # ← text curves along the outline
        "label_color": "#b91c3c",
        "label_size": 14,
    },
})
print("   'BUDAPEST CITY LIMITS' rendered along the boundary line")
wait()

# ═══ Scene 3 — sketch 003: 3D arc between two named locations ════════════════
banner("Scene 3 (003): 3D deck.gl arc — San Francisco ↔ Los Angeles")
send("zoom_to_bbox", {"bbox": [-124.5, 32.5, -116.0, 39.5]})
wait(2)
# Tilt into a cinematic view so the arc's altitude is visible.
# NOTE: pitch only — do NOT set_terrain 3d here. Globe projection can't
# render deck's interleaved ArcLayer (the engine falls back to a flat
# surface line); mercator + pitch is what makes the ribbon pop out.
send("set_pitch", {"pitch": 55, "bearing": 20, "duration": 2000})
wait(3)
send("add_arc", {
    "from": [-122.4194, 37.7749],
    "to": [-118.2437, 34.0522],
    "from_name": "San Francisco",
    "to_name": "Los Angeles",
    "name": "SF ↔ LA comparison",
    "curvature": 0.4,                 # → deck ArcLayer height 0.8
    "style": {
        "stroke_color": "#1d4ed8",
        "stroke_width": 5,
        "fill_color": "#1d4ed8",
    },
})
print("   3D arc flying over California (deck.gl ArcLayer), labeled dots at both ends")
wait(PAUSE * 2)
# Flatten the camera for the remaining scenes
send("set_pitch", {"pitch": 0, "bearing": 0, "duration": 1500})
wait(2)

# ═══ Scene 4 — sketch 005: gradient bands + hover ════════════════════════════
banner("Scene 4 (005): 'Spread over time' gradient bands — HOVER over them!")
CENTER = (-120.5, 38.0)  # somewhere in the Sierra foothills

# Irregular "fire spread" perimeters: per-vertex radial noise built from a
# few sinusoids (deterministic — no RNG), with the noise amplitude growing
# with the day so later perimeters are jaggier. Each day's boundary strictly
# contains the previous one (monotone radius per angle), so the bands nest
# like a real spread map — clearly arbitrary polygon data, not circles.


def spread_radius(theta, day):
    """Radius (deg) of the day-N fire perimeter in direction theta."""
    base = 0.11 * day
    wobble = (
        0.34 * math.sin(3 * theta + 1.7 * day)
        + 0.22 * math.sin(7 * theta + 0.6 * day)
        + 0.13 * math.sin(11 * theta + 2.9 * day)
        + 0.18 * math.sin(2 * theta - 1.1)      # persistent east-bias lobe
    )
    # Amplitude scaled so wobble can't invert nesting (< half the day step)
    return base * (1.0 + 0.38 * wobble)


def perimeter(day, n=72):
    """Closed ring of the day-N perimeter (lon cos-corrected)."""
    pts = []
    for i in range(n):
        th = 2 * math.pi * i / n
        # Take max over days<=day so rings always nest despite phase shifts
        r = max(spread_radius(th, d) for d in range(1, day + 1))
        pts.append([CENTER[0] + r * math.cos(th) * 1.25,
                    CENTER[1] + r * math.sin(th)])
    pts.append(pts[0])
    return pts


days = [1, 2, 3, 4, 5]
features = []
for day in days:
    outer = perimeter(day)
    if day == 1:
        geom = {"type": "Polygon", "coordinates": [outer]}
    else:
        inner = perimeter(day - 1)
        inner_reversed = inner[::-1]  # hole winds opposite
        geom = {"type": "Polygon", "coordinates": [outer, inner_reversed]}
    features.append({
        "type": "Feature",
        "properties": {"name": f"Day {day}", "day": day},
        "geometry": geom,
    })

send("zoom_to_bbox", {"bbox": [CENTER[0] - 1.3, CENTER[1] - 1.0,
                               CENTER[0] + 1.3, CENTER[1] + 1.0]})
wait(2)
send("add_polygon", {
    "geojson": json.dumps({"type": "FeatureCollection", "features": features}),
    "name": "Fire spread (day 1-5)",
    "style": {
        "stroke_color": "#7f1d1d",
        "stroke_width": 1,
        "color_by": {"property": "day", "ramp": "reds", "domain": [0, 5]},
        "label": True,                    # per-feature 'name' → Day N labels
        "label_placement": "center",
        "label_size": 11,
        "label_color": "#450a0a",
    },
})
print("   irregular nested spread perimeters, light→dark red by 'day' property")
print("   👉 move your mouse over a ring — it brightens (feature-state hover)")
wait(PAUSE * 2)

# ═══ Scene 5 — label retrofit via update_style ═══════════════════════════════
banner("Scene 5: Label an EXISTING asset live (update_style)")
res = send("add_polygon", {
    "geojson": json.dumps({
        "type": "Feature",
        "properties": {"name": "SURVEY ZONE 7"},
        "geometry": {"type": "Polygon", "coordinates": [[
            [-121.6, 38.5], [-121.2, 38.5], [-121.2, 38.75],
            [-121.6, 38.75], [-121.6, 38.5],
        ]]},
    }),
    "name": "Survey Zone 7",
    "style": {"fill_color": "#0e7a3f40", "stroke_color": "#0e7a3f", "stroke_width": 2},
})
zone_id = res.get("asset_id")
print("   plain polygon added (no label)…")
wait(3)
send("update_style", {
    "asset_id": zone_id,
    "style": {"label": True, "label_placement": "center",
              "label_color": "#0e7a3f", "label_size": 15},
})
print("   …and now labeled 'SURVEY ZONE 7' — no re-add needed")
wait()

# ═══ Scene 6 — glowing polygon (pulsing opacity) ═════════════════════════════
banner("Scene 6: GLOWING polygon — slowly fades opaque ↔ translucent")
send("add_polygon", {
    "geojson": json.dumps({
        "type": "Feature",
        "properties": {"name": "⚠ ALERT ZONE"},
        "geometry": {"type": "Polygon", "coordinates": [[
            [-120.05, 38.35], [-119.75, 38.42], [-119.62, 38.62],
            [-119.85, 38.78], [-120.15, 38.70], [-120.22, 38.48],
            [-120.05, 38.35],
        ]]},
    }),
    "name": "Alert Zone",
    "style": {
        "fill_color": "#e94560",
        "stroke_color": "#e94560",
        "stroke_width": 2,
        "label": True,
        "label_placement": "center",
        "label_color": "#b91c3c",
        "label_size": 14,
        "glow": {"period": 2.5, "min_opacity": 0.08, "max_opacity": 0.8},
    },
})
send("zoom_to_bbox", {"bbox": [-120.4, 38.25, -119.5, 38.9]})
print("   red polygon pulsing between nearly-invisible and opaque (2.5s cycle)")
wait(PAUSE * 2)

# ═══ Finale ══════════════════════════════════════════════════════════════════
banner("Finale: fit everything")
assets = requests.get(f"{SERVER}/api/maps/{map_id}/assets", timeout=10).json()
send("zoom_to_assets", {"asset_ids": [a["asset_id"] for a in assets]})
print(f"\n✅ Demo complete — {len(assets)} assets on the map.")
print(f"   Map stays live at: {map_url}")
