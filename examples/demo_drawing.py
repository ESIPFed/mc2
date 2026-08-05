"""Drawing tools demo — shows how to use the SDK to enable drawing and retrieve drawn features.

Run the server first:
    docker compose up  (or: cd server && uvicorn mapcontrol_server.main:app --reload --port 8080)

Then run this demo:
    python demo_drawing.py

What happens:
1. Creates a map and opens it in the browser
2. Adds a reference polygon so the map has something on it
3. Zooms to the area
4. Enables polygon drawing mode via the SDK
5. Waits for you to draw something on the map
6. Retrieves and prints the drawn features as GeoJSON
"""

import json
import time
import webbrowser

from mapcontrol import MapControl

SERVER = "http://localhost:8080"


def main():
    print("=== MapControl Drawing Tools Demo ===\n")

    # Connect to the server
    mc = MapControl(server_url=SERVER)
    session = mc.create_map()

    print(f"Map created: {session.map_id}")
    print(f"Map URL: {session.session_url}")
    print()

    # Open in browser
    webbrowser.open(session.session_url)
    time.sleep(3)  # Wait for map to load

    # Add a reference polygon so the map has context
    print("Adding a reference polygon near Austin, TX...")
    result = session.add_polygon(
        geojson={
            "type": "Feature",
            "properties": {"name": "Reference Area"},
            "geometry": {
                "type": "Polygon",
                "coordinates": [[
                    [-97.78, 30.25],
                    [-97.78, 30.30],
                    [-97.70, 30.30],
                    [-97.70, 30.25],
                    [-97.78, 30.25],
                ]],
            },
        },
        name="Reference Area",
        style={"fill_color": "#22c55e40", "stroke_color": "#22c55e", "stroke_width": 2},
    )
    print(f"  Added: {result.asset_id}")

    # Zoom to the area
    session.zoom_to_point(-97.74, 30.275, zoom=13)
    time.sleep(2)

    # Enable drawing mode
    print("\n--- Drawing Mode ---")
    print("Enabling POLYGON drawing mode...")
    print("  Draw a shape on the map by clicking vertices,")
    print("  then double-click to finish.")
    print()
    session.enable_drawing(mode="polygon")

    # Poll for drawn features
    print("Waiting for you to draw something...")
    print("(Press Ctrl+C when done drawing)\n")

    try:
        prev_count = 0
        while True:
            drawn = session.list_drawn_assets()
            if len(drawn) > prev_count:
                new_features = drawn[prev_count:]
                for asset in new_features:
                    print(f"  New drawn feature detected:")
                    print(f"    Asset ID: {asset.asset_id}")
                    print(f"    Type: {asset.asset_type}")
                    print(f"    Name: {asset.name}")
                    geojson = asset.geojson
                    if isinstance(geojson, str):
                        geojson = json.loads(geojson)
                    print(f"    Geometry type: {geojson.get('geometry', {}).get('type', 'unknown')}")
                    coords = geojson.get("geometry", {}).get("coordinates", [[]])
                    print(f"    Vertices: {len(coords[0]) if coords else 0}")
                    print()
                prev_count = len(drawn)
            time.sleep(1)
    except KeyboardInterrupt:
        pass

    # Disable drawing
    print("\nDisabling drawing mode...")
    session.disable_drawing()

    # Print all drawn features as GeoJSON
    drawn = session.list_drawn_assets()
    print(f"\n=== Results ===")
    print(f"Total drawn features: {len(drawn)}")

    if drawn:
        fc = session.get_drawn_features_geojson()
        print(f"\nGeoJSON FeatureCollection:")
        print(json.dumps(fc, indent=2))

    # Show all assets on the map
    all_assets = session.list_assets()
    print(f"\nAll assets on map ({len(all_assets)}):")
    for asset in all_assets:
        drawn_tag = " [DRAWN]" if asset.asset_type.startswith("drawn_") else ""
        print(f"  {asset.asset_id[:8]}... {asset.asset_type}{drawn_tag} - {asset.name}")

    print("\nDone. The map is still open in your browser.")
    print(f"Map URL: {session.session_url}")


if __name__ == "__main__":
    main()
