#!/usr/bin/env python3
"""Demo: 2D/3D terrain toggle.

Shows the map in 2D (default), zooms to a mountainous area,
then toggles to 3D with terrain and sky, waits, then back to 2D.

Usage:
    # Start server first:  docker compose up  (or: cd server && uvicorn mapcontrol_server.main:app --reload --port 8000)
    # Then run:
    python demo_terrain.py
"""

import time
import webbrowser

from mapcontrol import MapControl

SERVER = "http://localhost:8000"


def main():
    mc = MapControl(SERVER)

    # Create a map and open it in the browser
    session = mc.create_map()
    print(f"Map created: {session.url}")
    print(f"Session URL: {session.session_url}")
    webbrowser.open(session.session_url)

    # Wait for the browser to load
    time.sleep(3)

    # Zoom to the Swiss Alps (great for terrain demo)
    print("Zooming to Swiss Alps...")
    session.zoom_to_point(lon=8.0, lat=46.8, zoom=10)
    time.sleep(2)

    # Switch to satellite basemap for dramatic effect
    print("Switching to satellite basemap...")
    session.set_basemap("satellite")
    time.sleep(2)

    # Enable 3D terrain
    print("Enabling 3D terrain...")
    session.set_terrain("3d")
    time.sleep(4)

    # Add a polygon over a valley
    print("Adding polygon over Lauterbrunnen Valley...")
    session.add_polygon(
        geojson={
            "type": "Feature",
            "properties": {"name": "Lauterbrunnen Valley"},
            "geometry": {
                "type": "Polygon",
                "coordinates": [[
                    [7.88, 46.55], [7.95, 46.55], [7.95, 46.60],
                    [7.88, 46.60], [7.88, 46.55]
                ]]
            }
        },
        name="Lauterbrunnen Valley",
        style={"fill_color": "#e9456050", "stroke_color": "#e94560", "stroke_width": 2},
    )
    time.sleep(1)

    # Zoom closer to see terrain detail
    print("Zooming to valley...")
    session.zoom_to_point(lon=7.91, lat=46.575, zoom=13)
    time.sleep(4)

    # Switch back to 2D
    print("Switching back to 2D...")
    session.set_terrain("2d")
    time.sleep(3)

    # Back to 3D one more time
    print("Back to 3D for the finale...")
    session.set_terrain("3d")
    time.sleep(3)

    print("Done! The map is in 3D mode.")
    print("Try right-click + drag to rotate, or scroll to zoom.")
    print(f"Map URL: {session.session_url}")


if __name__ == "__main__":
    main()
