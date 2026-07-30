"""Test the Playwright screenshot feature — no browser needed."""

import httpx
import json

BASE = "http://localhost:8888"

print("=== Screenshot Test (Playwright, no browser) ===\n")

# 1. Create map
print("1. Creating map...")
r = httpx.post(f"{BASE}/api/maps")
map_id = r.json()["map_id"]
print(f"   Map ID: {map_id}")

# 2. Create session
print("2. Creating session...")
r = httpx.post(f"{BASE}/api/maps/{map_id}/sessions")
sid = r.json()["user_session_id"]
print(f"   Session: {sid}")

# 3. Add a polygon (Austin TX)
print("3. Adding polygon...")
r = httpx.post(f"{BASE}/api/maps/{map_id}/events", json={
    "type": "add_polygon",
    "data": {
        "geojson": json.dumps({
            "type": "Feature",
            "properties": {"name": "Test Area"},
            "geometry": {
                "type": "Polygon",
                "coordinates": [[
                    [-97.85, 30.20], [-97.65, 30.20],
                    [-97.65, 30.35], [-97.85, 30.35],
                    [-97.85, 30.20]
                ]]
            }
        }),
        "style": {"fill_color": "#ff000060", "stroke_color": "#ff0000", "stroke_width": 3}
    },
    "user_session_id": sid,
})
print(f"   Asset: {r.json().get('asset_id', 'n/a')}")

# 4. Add a point
print("4. Adding point marker...")
r = httpx.post(f"{BASE}/api/maps/{map_id}/events", json={
    "type": "add_point",
    "data": {
        "geojson": json.dumps({
            "type": "Feature",
            "properties": {"name": "Capitol"},
            "geometry": {"type": "Point", "coordinates": [-97.7405, 30.2747]}
        }),
        "style": {"fill_color": "#00ff00", "stroke_color": "#fff", "stroke_width": 2}
    },
    "user_session_id": sid,
})
print(f"   Asset: {r.json().get('asset_id', 'n/a')}")

# 5. Zoom to area
print("5. Zooming to Austin...")
httpx.post(f"{BASE}/api/maps/{map_id}/events", json={
    "type": "zoom_to_point",
    "data": {"lon": -97.74, "lat": 30.27, "zoom": 11},
    "user_session_id": sid,
})

# 6. Take screenshot via Playwright (no browser needed)
print("6. Taking screenshot via Playwright (this may take 3-5 seconds)...")
r = httpx.post(
    f"{BASE}/api/maps/{map_id}/sessions/{sid}/screenshot?method=playwright",
    timeout=30.0
)
print(f"   Status: {r.status_code}")
if r.status_code == 200:
    result = r.json()
    print(f"   Screenshot URL: {result['full_url']}")
    print(f"   Filename: {result['filename']}")
    print(f"\n   Open in browser: {result['full_url']}")
else:
    print(f"   Error: {r.text}")

print("\nDone.")
