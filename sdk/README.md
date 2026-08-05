# mapcontrol

Python client SDK for the MapControl live-map server.

MapControl is a headless map server (FastAPI + MapLibre) that renders live,
shareable browser maps you drive from code: draw shapes, add GeoTIFF overlays,
fly the camera, take screenshots, switch basemaps and themes — all over a
simple REST/WebSocket API. This package is the typed Python client for it.

## Install

```bash
pip install mapcontrol
```

## Quick start

```python
from mapcontrol import MapControl

mc = MapControl(server_url="http://localhost:8000")
session = mc.create_map(name="demo")
print(session.map_url)          # open this in a browser — updates live

session.zoom_to(-122.42, 37.77, zoom=12)
session.add_polygon([[-122.5, 37.7], [-122.4, 37.7], [-122.4, 37.8]])
session.set_basemap("satellite")
session.set_theme("dark")

shot = session.take_screenshot()  # PNG of the current view
```

## What's in the box

| Object | Purpose |
| --- | --- |
| `MapControl` | Entry point — connects to a server, creates/loads maps |
| `MapSession` | A live map: assets, camera, GeoTIFFs, screenshots, themes, terrain |
| `Asset`, `Viewport`, `Style`, `ScreenshotResult` | Typed models for the API |

## Running the server

The server ships as a container:

```bash
docker run -p 8000:8000 ghcr.io/ama-labs/mapcontrol-server:latest
```

See the server repository for configuration (basemaps, providers, auth, MCP).

## License

Apache-2.0
