#!/usr/bin/env python3
"""
Map Control Multi-Session Demo
===============================
Demonstrates that a second user can open the same map and see
all the same assets — the session restore feature in action.

1. Creates a map, opens it in browser tab #1
2. Adds some assets and zooms around
3. Opens a SECOND browser tab (new session, same map)
4. The second tab restores the full state automatically
5. Then sends a command that both tabs see in real-time

Usage:
  python demo_multi_session.py
"""

import sys
import os
import time
import webbrowser

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "sdk"))

from mapcontrol import MapControl, Style

SERVER_URL = "http://localhost:8888"
PAUSE = 3


def narrate(msg: str):
    print(f"\n{'─' * 60}")
    print(f"  ▶ {msg}")
    print(f"{'─' * 60}")
    time.sleep(PAUSE)


def main():
    print("\n🖥️  Multi-Session Demo")
    print("=" * 60)
    print("  This demo shows that multiple browser tabs share the")
    print("  same map state and receive real-time updates together.")
    print("=" * 60)

    mc = MapControl(SERVER_URL)

    # ── 1. Create map + first session ────────────────────────────
    narrate("Creating a map and first user session...")
    session1 = mc.create_map()
    print(f"  Map ID:     {session1.map_id}")
    print(f"  Session 1:  {session1.user_session_id[:8]}...")
    print(f"  URL:        {session1.session_url}")

    # ── 2. Open first browser tab ────────────────────────────────
    narrate("Opening FIRST browser tab...")
    webbrowser.open(session1.session_url)
    time.sleep(4)  # let it load and connect WebSocket

    # ── 3. Add assets via session 1 ──────────────────────────────
    narrate("Zooming to San Francisco...")
    session1.zoom_to_point(lon=-122.4194, lat=37.7749, zoom=13)

    narrate("Adding Golden Gate Park (green polygon)...")
    ggp = session1.add_polygon(
        geojson="""{
            "type": "Feature",
            "properties": {},
            "geometry": {
                "type": "Polygon",
                "coordinates": [[
                    [-122.5108, 37.7694],
                    [-122.5108, 37.7737],
                    [-122.4534, 37.7737],
                    [-122.4534, 37.7694],
                    [-122.5108, 37.7694]
                ]]
            }
        }""",
        name="Golden Gate Park",
        style=Style(fill_color="#00aa0044", stroke_color="#00aa00", stroke_width=3),
    )
    print(f"  Asset: {ggp.asset_id[:8]}...")

    narrate("Adding Fishermans Wharf area (orange polygon)...")
    wharf = session1.add_polygon(
        geojson="""{
            "type": "Feature",
            "properties": {},
            "geometry": {
                "type": "Polygon",
                "coordinates": [[
                    [-122.4220, 37.8060],
                    [-122.4220, 37.8110],
                    [-122.4080, 37.8110],
                    [-122.4080, 37.8060],
                    [-122.4220, 37.8060]
                ]]
            }
        }""",
        name="Fishermans Wharf",
        style=Style(fill_color="#ff880044", stroke_color="#ff8800", stroke_width=3),
    )
    print(f"  Asset: {wharf.asset_id[:8]}...")

    narrate("Adding Market Street path (blue line)...")
    market = session1.add_path(
        geojson="""{
            "type": "Feature",
            "properties": {},
            "geometry": {
                "type": "LineString",
                "coordinates": [
                    [-122.4176, 37.7692],
                    [-122.4100, 37.7750],
                    [-122.4000, 37.7820],
                    [-122.3940, 37.7940]
                ]
            }
        }""",
        name="Market Street",
        style=Style(stroke_color="#0066ff", stroke_width=4),
    )
    print(f"  Asset: {market.asset_id[:8]}...")

    narrate("Zooming to fit all assets...")
    session1.zoom_to_assets([ggp.asset_id, wharf.asset_id, market.asset_id])

    # ── 4. Now open a SECOND session on the same map ─────────────
    print("\n" + "=" * 60)
    print("  Now watch: a SECOND browser tab will open on the")
    print("  SAME map. It should restore all 3 assets and the")
    print("  viewport automatically — no replay, just restore.")
    print("=" * 60)

    narrate("Creating second user session on the same map...")
    session2 = mc.connect_map(session1.map_id)
    print(f"  Session 2:  {session2.user_session_id[:8]}...")
    print(f"  URL:        {session2.session_url}")

    narrate("Opening SECOND browser tab...")
    webbrowser.open(session2.session_url)
    time.sleep(5)  # let it load and restore

    print("\n  ✅ Both tabs should now show the same map with all assets.")

    # ── 5. Zoom is INDEPENDENT — only the requesting tab zooms ───
    narrate("Zooming Session 1 to Fishermans Wharf — ONLY Tab 1 should zoom...")
    session1.zoom_to_assets([wharf.asset_id])

    time.sleep(PAUSE)

    narrate("Zooming Session 2 to Golden Gate Park — ONLY Tab 2 should zoom...")
    session2.zoom_to_assets([ggp.asset_id])

    time.sleep(PAUSE)

    print("\n  ✅ Each tab zoomed independently — different viewports now.")

    # ── 6. Assets are SHARED — both tabs update ──────────────────
    narrate("Adding a NEW polygon via Session 2 — watch BOTH tabs update...")
    pier39 = session2.add_polygon(
        geojson="""{
            "type": "Feature",
            "properties": {},
            "geometry": {
                "type": "Polygon",
                "coordinates": [[
                    [-122.4120, 37.8080],
                    [-122.4120, 37.8130],
                    [-122.4070, 37.8130],
                    [-122.4070, 37.8080],
                    [-122.4120, 37.8080]
                ]]
            }
        }""",
        name="Pier 39 (added by Session 2)",
        style=Style(fill_color="#cc00cc44", stroke_color="#cc00cc", stroke_width=3),
    )
    print(f"  New asset from Session 2: {pier39.asset_id[:8]}...")

    narrate("Zooming out to see everything...")
    session1.zoom_to_assets([ggp.asset_id, wharf.asset_id, market.asset_id, pier39.asset_id])

    narrate("Switching Tab 1 to satellite — Tab 2 stays on OSM...")
    session1.set_basemap("satellite")

    time.sleep(PAUSE)

    narrate("Switching Tab 1 back to OSM...")
    session1.set_basemap("osm")

    # ── Done ─────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("  Multi-Session Demo Complete")
    print("=" * 60)
    print(f"\n  Tab 1: {session1.session_url}")
    print(f"  Tab 2: {session2.session_url}")
    print(f"  Map ID: {session1.map_id}")
    print(f"\n  Both tabs share assets. Basemap is per-user.")
    print("  Try refreshing a tab — it will restore the state.")
    print("\n  Press Ctrl+C to exit.\n")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nBye!")


if __name__ == "__main__":
    main()
