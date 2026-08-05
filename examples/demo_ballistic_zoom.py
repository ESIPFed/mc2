#!/usr/bin/env python3
"""
MapLibre FlyTo World Tour Demo
================================
Showcases MapLibre GL JS native flyTo animation — the van Wijk
smooth zoom algorithm. Flies between 10 cities around the world.

Prerequisites:
  - Server running: docker compose up  (or: cd server && uvicorn mapcontrol_server.main:app --reload --port 8080)
  - SDK installed: cd sdk && pip install -e .

Usage:
  python demo_ballistic_zoom.py
"""

import sys
import os
import time
import webbrowser

# Add SDK to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "sdk"))

from mapcontrol import MapControl

SERVER_URL = "http://localhost:8080"

# Time to wait for each animation to complete before sending the next
WAIT = 6


# World tour stops — name, lon, lat, zoom
STOPS = [
    ("New York City, USA",       -74.0060,  40.7128, 13),
    ("London, UK",                -0.1276,  51.5074, 13),
    ("Tokyo, Japan",             139.6917,  35.6895, 13),
    ("Sydney, Australia",        151.2093, -33.8688, 13),
    ("Cape Town, South Africa",   18.4241, -33.9249, 13),
    ("Rio de Janeiro, Brazil",   -43.1729, -22.9068, 13),
    ("Budapest, Hungary",         19.0402,  47.4979, 13),
    ("Dallas, Texas, USA",       -96.7970,  32.7767, 13),
    ("Dubai, UAE",                55.2708,  25.2048, 13),
    ("Reykjavik, Iceland",       -21.8174,  64.1466, 13),
]


def banner(msg: str):
    """Print a step banner."""
    print(f"\n{'━' * 60}")
    print(f"  🌍  {msg}")
    print(f"{'━' * 60}")


def main():
    print()
    print("=" * 60)
    print("  🚀  MAPLIBRE FLYTO WORLD TOUR")
    print("  Smooth van Wijk algorithm — no tile artifacts")
    print("=" * 60)

    mc = MapControl(SERVER_URL)

    # Create map and open browser
    banner("Creating map...")
    session = mc.create_map()
    print(f"  Map URL: {session.session_url}")

    webbrowser.open(session.session_url)
    print("  Waiting for browser to load...")
    time.sleep(4)

    # Start at the first stop
    first = STOPS[0]
    banner(f"Starting at {first[0]}")
    session.zoom_to_point(lon=first[1], lat=first[2], zoom=first[3])
    time.sleep(WAIT)

    # Fly through each subsequent stop
    for i in range(1, len(STOPS)):
        prev = STOPS[i - 1]
        curr = STOPS[i]
        banner(f"{prev[0]}  →  {curr[0]}")
        session.zoom_to_point(lon=curr[1], lat=curr[2], zoom=curr[3])
        time.sleep(WAIT)

    # Final: back to New York to complete the loop
    last = STOPS[-1]
    first = STOPS[0]
    banner(f"{last[0]}  →  {first[0]} (full circle)")
    session.zoom_to_point(lon=first[1], lat=first[2], zoom=first[3])
    time.sleep(WAIT)

    print()
    print("=" * 60)
    print("  ✅  World tour complete!")
    print(f"  Map URL: {session.session_url}")
    print("=" * 60)
    print("\n  The map is still live. Press Ctrl+C to exit.\n")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nBye! 👋")


if __name__ == "__main__":
    main()
