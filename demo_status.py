"""Demo: attention-lifecycle styling (style.status) on survey stations.

Simulates the EOGPT Cawndilla-2 pixel-drill flow: drop six stations, then
walk them one by one — the station being processed PULSES (status=active),
finished stations flip to a solid success look (status=done), and when the
analysis pivots away, abandoned stations GRAY OUT (status=muted).

Run:  python demo_status.py   (server on http://localhost:8000)
Open the printed URL in a browser to watch.
"""

import time

import httpx

BASE = "http://localhost:8000"

STATIONS = [
    ("P1", -143.20, 32.10), ("P2", -143.05, 32.18), ("P3", -143.12, 31.98),
    ("C1", -143.45, 32.30), ("C2", -142.80, 32.35), ("C3", -142.90, 31.85),
]


def point_geojson(name, lon, lat):
    return (
        '{"type":"Feature","properties":{"name":"%s"},'
        '"geometry":{"type":"Point","coordinates":[%f,%f]}}' % (name, lon, lat)
    )


def main():
    c = httpx.Client(base_url=BASE, timeout=30)
    map_id = c.post("/api/maps").json()["map_id"]
    print(f"Map: {BASE}/map/{map_id}")

    def event(type_, data):
        r = c.post(f"/api/maps/{map_id}/events", json={"type": type_, "data": data})
        r.raise_for_status()
        return r.json()

    # Drop the stations (labeled points)
    ids = {}
    for name, lon, lat in STATIONS:
        resp = event("add_point", {
            "geojson": point_geojson(name, lon, lat), "name": name,
            "style": {"fill_color": "#38bdf8", "stroke_color": "#e0f2fe",
                      "stroke_width": 2, "label": True},
        })
        ids[name] = resp["asset_id"]
    event("zoom_to_bbox", {"bbox": [-143.7, 31.7, -142.6, 32.5]})
    print("Stations placed. Starting the drill loop...")
    time.sleep(3)

    # Drill P1-P3: pulse while "processing", then mark done
    for name in ("P1", "P2", "P3"):
        print(f"  {name}: processing (pulse)...")
        event("update_style", {"asset_id": ids[name], "style": {"status": "active"}})
        time.sleep(5)  # pretend to compute
        print(f"  {name}: done.")
        event("update_style", {"asset_id": ids[name], "style": {"status": "done"}})
        time.sleep(1)

    # Pivot: the control stations are no longer under consideration
    print("Pivoting — muting control stations C1-C3...")
    time.sleep(2)
    for name in ("C1", "C2", "C3"):
        event("update_style", {"asset_id": ids[name], "style": {"status": "muted"}})

    print("Demo complete: P1-P3 solid green, C1-C3 grayed out.")


if __name__ == "__main__":
    main()
