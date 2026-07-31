#!/usr/bin/env python3
"""
Map Control Demo Script
=======================
Creates a map, opens it in your browser, then walks through
the features one by one with pauses so you can watch it happen.

Prerequisites:
  - Server running: docker compose up  (or: cd server && uvicorn mapcontrol_server.main:app --reload --port 8000)
  - SDK installed: cd sdk && pip install -e .

Usage:
  python demo.py
"""

import sys
import os
import time
import webbrowser
import subprocess

# Add SDK to path for the demo
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "sdk"))

from mapcontrol import MapControl, Style

SERVER_URL = "http://localhost:8000"
PAUSE = 3  # seconds between actions


def narrate(msg: str):
    """Print a narrated step with a visual separator."""
    print(f"\n{'─' * 60}")
    print(f"  ▶ {msg}")
    print(f"{'─' * 60}")
    time.sleep(PAUSE)


def main():
    print("\n🚀 Map Control Demo")
    print("=" * 60)

    mc = MapControl(SERVER_URL)

    # ── 1. Create a map ──────────────────────────────────────────
    narrate("Creating a new map...")
    session = mc.create_map()
    print(f"  Map ID: {session.map_id}")
    print(f"  URL: {session.session_url}")

    # ── 2. Open in browser ───────────────────────────────────────
    narrate("Opening map in your browser...")
    webbrowser.open(session.session_url)
    time.sleep(3)  # extra pause to let the browser load

    # ── 3. Zoom to Austin, TX ────────────────────────────────────
    narrate("Zooming to Austin, Texas...")
    session.zoom_to_point(lon=-97.7431, lat=30.2672, zoom=12)

    # ── 4. Add a polygon — UT Austin campus area ─────────────────
    narrate("Adding a polygon: UT Austin campus area (red)...")
    ut_campus = session.add_polygon(
        geojson="""{
            "type": "Feature",
            "properties": {"name": "UT Austin Campus"},
            "geometry": {
                "type": "Polygon",
                "coordinates": [[
                    [-97.7420, 30.2830],
                    [-97.7420, 30.2900],
                    [-97.7320, 30.2900],
                    [-97.7320, 30.2830],
                    [-97.7420, 30.2830]
                ]]
            }
        }""",
        name="UT Austin Campus",
        style=Style(fill_color="#cc000044", stroke_color="#cc0000", stroke_width=3),
    )
    print(f"  Asset ID: {ut_campus.asset_id}")

    # ── 5. Add another polygon — Texas Capitol ───────────────────
    narrate("Adding a polygon: Texas State Capitol (blue)...")
    capitol = session.add_polygon(
        geojson="""{
            "type": "Feature",
            "properties": {"name": "Texas Capitol"},
            "geometry": {
                "type": "Polygon",
                "coordinates": [[
                    [-97.7410, 30.2740],
                    [-97.7410, 30.2760],
                    [-97.7380, 30.2760],
                    [-97.7380, 30.2740],
                    [-97.7410, 30.2740]
                ]]
            }
        }""",
        name="Texas State Capitol",
        style=Style(fill_color="#0000cc44", stroke_color="#0000cc", stroke_width=3),
    )
    print(f"  Asset ID: {capitol.asset_id}")

    # ── 6. Add a path — Lady Bird Lake Trail ─────────────────────
    narrate("Adding a path: Lady Bird Lake Trail (green)...")
    trail = session.add_path(
        geojson="""{
            "type": "Feature",
            "properties": {"name": "Lady Bird Lake Trail"},
            "geometry": {
                "type": "LineString",
                "coordinates": [
                    [-97.7700, 30.2600],
                    [-97.7600, 30.2580],
                    [-97.7500, 30.2570],
                    [-97.7400, 30.2580],
                    [-97.7300, 30.2600],
                    [-97.7200, 30.2590],
                    [-97.7100, 30.2610]
                ]
            }
        }""",
        name="Lady Bird Lake Trail",
        style=Style(fill_color="#00880044", stroke_color="#008800", stroke_width=4),
    )
    print(f"  Asset ID: {trail.asset_id}")

    # ── 7. Zoom to fit all assets ────────────────────────────────
    narrate("Zooming to fit ALL assets in view...")
    session.zoom_to_assets([ut_campus.asset_id, capitol.asset_id, trail.asset_id])

    # ── 8. List all assets ───────────────────────────────────────
    narrate("Listing all assets on the map...")
    assets = session.list_assets()
    for a in assets:
        vis = "👁" if a.visible else "🚫"
        print(f"  {vis}  {a.name or '(unnamed)'} [{a.asset_type}] — {a.asset_id[:8]}...")

    # ── 9. Toggle visibility — hide the trail ────────────────────
    narrate("Hiding the Lady Bird Lake Trail...")
    session.set_visibility(trail.asset_id, visible=False)

    time.sleep(PAUSE)

    narrate("Showing it again...")
    session.set_visibility(trail.asset_id, visible=True)

    # ── 10. Update style — make UT campus gold ───────────────────
    narrate("Changing UT Austin campus to burnt orange...")
    session.update_style(
        ut_campus.asset_id,
        Style(fill_color="#bf560044", stroke_color="#bf5600", stroke_width=4),
    )

    # ── 11. Switch to satellite ──────────────────────────────────
    narrate("Switching to satellite basemap...")
    session.set_basemap("satellite")

    time.sleep(PAUSE)

    narrate("Switching back to OSM...")
    session.set_basemap("osm")

    # ── 12. Zoom to just the Capitol ─────────────────────────────
    narrate("Zooming in on the Texas State Capitol...")
    session.zoom_to_assets([capitol.asset_id])

    time.sleep(PAUSE)

    # ── 13. Zoom out ─────────────────────────────────────────────
    narrate("Zooming out 3 levels...")
    session.zoom_out(levels=3)

    # ── 14. Delete an asset ──────────────────────────────────────
    narrate("Deleting the Texas Capitol polygon...")
    session.delete_asset(capitol.asset_id)

    # ── 15. Final zoom to remaining assets ───────────────────────
    narrate("Final view: zooming to remaining assets...")
    session.zoom_to_assets([ut_campus.asset_id, trail.asset_id])

    # ── Done ─────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("✅ Demo complete!")
    print(f"   Map URL: {session.session_url}")
    print(f"   Map ID:  {session.map_id}")
    print("=" * 60)
    print("\nThe map is still live — try sending more commands via the SDK!")
    print("Press Ctrl+C to exit.\n")

    # Keep alive so user can interact
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nBye! 👋")


if __name__ == "__main__":
    main()
