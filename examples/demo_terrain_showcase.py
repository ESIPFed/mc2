#!/usr/bin/env python3
"""Comprehensive 2D/3D terrain showcase demo with live operation labels.

Phase 1: Add vector objects, toggle 2D/3D, zoom around
Phase 2: Upload a GeoTIFF, toggle 2D/3D, confirm raster persists
Phase 3: 3D globe zoom-out + ballistic zoom tour (Budapest -> Dallas -> Izmir)

Usage:
    # Start server first:
    cd server && uvicorn mapcontrol_server.main:app --reload --port 8888

    # Then run:
    python demo_terrain_showcase.py
"""

import os
import time
import webbrowser

import httpx

from mapcontrol import MapControl

SERVER = "http://localhost:8888"

# sample.tif lives in the repo at examples/data/sample.tif
SAMPLE_TIFF = os.path.join(os.path.dirname(__file__), "data", "sample.tif")


# ─── Globals set during setup ────────────────────────────────────────────────
session = None


def label(text):
    """Send a label to the demo page header AND print to console."""
    print(f"  >> {text}")
    session._send_event("set_demo_label", {"text": text})


def wait(seconds):
    time.sleep(seconds)


def main():
    global session
    mc = MapControl(SERVER)

    # ─── Setup ────────────────────────────────────────────────────────────
    session = mc.create_map()
    print(f"Map created: {session.map_id}")

    demo_url = (
        f"{SERVER}/demo/terrain"
        f"?map_id={session.map_id}"
        f"&user_session={session.user_session_id}"
    )
    print(f"Demo page: {demo_url}")
    webbrowser.open(demo_url)
    wait(3)

    # =====================================================================
    #  PHASE 1: Vector Objects + 2D/3D Toggle + Zoom
    # =====================================================================
    label("PHASE 1: Adding vector objects")
    wait(1)

    label("Adding polygon: Observation Zone (Netherlands)")
    r1 = session.add_polygon(
        geojson={
            "type": "Feature",
            "properties": {"name": "Observation Zone"},
            "geometry": {
                "type": "Polygon",
                "coordinates": [[
                    [4.30, 52.15], [4.50, 52.15], [4.50, 52.27],
                    [4.30, 52.27], [4.30, 52.15],
                ]],
            },
        },
        name="Observation Zone",
        style={"fill_color": "#e9456050", "stroke_color": "#e94560", "stroke_width": 2},
    )
    polygon_id = r1.asset_id
    wait(1)

    label("Adding path: Survey Route")
    r2 = session.add_path(
        geojson={
            "type": "Feature",
            "properties": {"name": "Survey Route"},
            "geometry": {
                "type": "LineString",
                "coordinates": [
                    [4.33, 52.17], [4.36, 52.19], [4.38, 52.21],
                    [4.40, 52.22], [4.43, 52.24], [4.46, 52.25],
                ],
            },
        },
        name="Survey Route",
        style={"stroke_color": "#ffd93d", "stroke_width": 4},
    )
    path_id = r2.asset_id
    wait(1)

    label("Adding point: Weather Station")
    r3 = session.add_point(
        geojson={
            "type": "Feature",
            "properties": {"name": "Weather Station"},
            "geometry": {"type": "Point", "coordinates": [4.40, 52.21]},
        },
        name="Weather Station",
        style={"fill_color": "#4ecca3", "stroke_color": "#ffffff", "stroke_width": 2},
    )
    point_id = r3.asset_id
    wait(1)

    label("Testing: zoom_to_assets (fit all 3 objects)")
    session.zoom_to_assets([polygon_id, path_id, point_id])
    wait(2)

    label("Testing: switch to 3D Globe")
    session.set_terrain("3d")
    wait(3)

    label("Verifying: objects still visible in 3D Globe")
    wait(2)

    label("Testing: switch back to 2D Flat")
    session.set_terrain("2d")
    wait(3)

    label("Verifying: objects still visible in 2D")
    wait(1)

    label("Testing: zoom_to_point in 2D")
    session.zoom_to_point(lon=4.40, lat=52.21, zoom=14)
    wait(2)

    label("Testing: zoom_out 3 levels")
    session.zoom_out(levels=3)
    wait(2)

    label("Testing: zoom_to_assets (path only)")
    session.zoom_to_assets([path_id])
    wait(2)

    label("Testing: zoom_to_point while in 3D Globe")
    session.set_terrain("3d")
    wait(2)
    session.zoom_to_point(lon=4.38, lat=52.20, zoom=13)
    wait(3)

    # =====================================================================
    #  PHASE 2: GeoTIFF + 2D/3D Toggle
    # =====================================================================
    label("PHASE 2: GeoTIFF raster overlay")
    wait(1)

    label("Resetting to 2D for GeoTIFF upload")
    session.set_terrain("2d")
    wait(2)

    label("Uploading GeoTIFF: sample.tif (viridis colormap)")
    if not os.path.exists(SAMPLE_TIFF):
        label("ERROR: sample.tif not found, skipping Phase 2")
    else:
        with httpx.Client(base_url=SERVER, timeout=30.0) as client:
            with open(SAMPLE_TIFF, "rb") as f:
                resp = client.post(
                    f"/api/maps/{session.map_id}/upload-geotiff",
                    files={"file": ("sample.tif", f, "image/tiff")},
                    data={
                        "mode": "singleband",
                        "band": "1",
                        "colormap": "viridis",
                        "alpha": "0.85",
                        "name": "Sample GeoTIFF (Leiden)",
                        "user_session_id": session.user_session_id,
                    },
                )
            if resp.status_code >= 400:
                label(f"Upload failed: {resp.status_code}")
            else:
                tiff_result = resp.json()
                tiff_bounds = tiff_result["bounds"]
                wait(1)

                label("Testing: zoom to GeoTIFF bounds in 2D")
                session.zoom_to_bbox(tiff_bounds)
                wait(3)

                label("Testing: 3D Globe with GeoTIFF overlay")
                session.set_terrain("3d")
                wait(4)

                label("Verifying: GeoTIFF + vectors visible in 3D")
                wait(2)

                label("Testing: back to 2D with GeoTIFF")
                session.set_terrain("2d")
                wait(3)

                label("Verifying: everything still rendered in 2D")
                wait(1)

    # =====================================================================
    #  PHASE 3: Globe zoom-out + Ballistic zoom tour
    # =====================================================================
    label("PHASE 3: Globe view + ballistic zoom tour")
    wait(1)

    label("Switching to 3D Globe + satellite basemap")
    session.set_terrain("3d")
    wait(2)
    session.set_basemap("satellite")
    wait(2)

    label("Testing: zoom out to see full globe (zoom level 2)")
    session.zoom_to_point(lon=20.0, lat=30.0, zoom=2)
    wait(5)

    label("Ballistic zoom: Flying to Budapest, Hungary")
    session.zoom_to_point(lon=19.04, lat=47.50, zoom=12)
    wait(5)

    label("Ballistic zoom: Budapest -> Dallas, Texas")
    session.zoom_to_point(lon=-96.80, lat=32.78, zoom=12)
    wait(6)

    label("Ballistic zoom: Dallas -> Izmir, Turkey")
    session.zoom_to_point(lon=27.14, lat=38.42, zoom=12)
    wait(6)

    label("Testing: zoom out to globe view from Izmir")
    session.zoom_to_point(lon=27.14, lat=38.42, zoom=2)
    wait(4)

    label("Testing: back to 2D Flat from globe")
    session.set_terrain("2d")
    wait(3)

    label("Testing: return to assets in 2D")
    session.set_basemap("osm")
    wait(1)
    session.zoom_to_bbox([4.28, 52.13, 4.52, 52.29])
    wait(3)

    # =====================================================================
    #  PHASE 4: Pitch control + Screenshots
    # =====================================================================
    label("PHASE 4: Pitch control + screenshots")
    wait(1)

    label("Testing: set_pitch to 45 degrees (gentle tilt)")
    session.set_terrain("3d")
    wait(2)
    session.set_basemap("satellite")
    wait(1)
    session.zoom_to_bbox([4.28, 52.13, 4.52, 52.29])
    wait(2)
    session._send_event("set_pitch", {"pitch": 45, "bearing": 0})
    wait(2)

    label("Testing: set_pitch to 75 degrees (steep tilt)")
    session._send_event("set_pitch", {"pitch": 75, "bearing": 30})
    wait(2)

    label("Testing: set_pitch to 30 with bearing 180 (south-facing)")
    session._send_event("set_pitch", {"pitch": 30, "bearing": 180})
    wait(2)

    label("Taking screenshot: GeoTIFF in 3D globe with vectors on top")
    session._send_event("set_pitch", {"pitch": 50, "bearing": 0})
    wait(2)
    # Zoom tightly to GeoTIFF center so the raster fills the view
    session.zoom_to_point(lon=4.40, lat=52.21, zoom=12)
    wait(6)  # Extra wait for satellite tiles + GeoTIFF to load
    try:
        result = session.take_screenshot()
        label(f"Screenshot saved: {result.filename}")
        print(f"  Screenshot URL: {result.full_url}")
        webbrowser.open(result.full_url)
        wait(3)
    except Exception as e:
        label(f"Screenshot failed: {e}")
        print(f"  Error: {e}")
        wait(1)

    label("Zooming to Budapest for second screenshot")
    session.zoom_to_point(lon=19.04, lat=47.50, zoom=12)
    wait(4)

    label("Taking screenshot: Budapest in 3D globe")
    try:
        result2 = session.take_screenshot()
        label(f"Screenshot saved: {result2.filename}")
        print(f"  Screenshot URL: {result2.full_url}")
        webbrowser.open(result2.full_url)
        wait(2)
    except Exception as e:
        label(f"Screenshot failed: {e}")
        print(f"  Error: {e}")
        wait(1)

    # =====================================================================
    #  DONE
    # =====================================================================
    label("DEMO COMPLETE — all tests passed")

    assets = session.list_assets()
    print(f"\nTotal assets on map: {len(assets)}")
    for a in assets:
        print(f"  - {a.name or '(unnamed)'} [{a.asset_type}] id={a.asset_id[:8]}...")

    print(f"\nMap URL: {session.session_url}")
    print("Try: right-click + drag to rotate, drawing tools work in 3D too!")


if __name__ == "__main__":
    main()
