# MapControl <sup>(MC²)</sup>

**Map Control × Model Context Protocol** — a live, shareable web map that AI agents (and humans) drive from code.

[![Discord](https://img.shields.io/badge/Discord-join%20the%20chat-5865F2?logo=discord&logoColor=white)](https://discord.gg/Q4BB4uF68B)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![MCP](https://img.shields.io/badge/MCP-Streamable%20HTTP-black?logo=anthropic&logoColor=white)](https://modelcontextprotocol.io/)
[![FastAPI](https://img.shields.io/badge/FastAPI-async-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![MapLibre](https://img.shields.io/badge/MapLibre-GL-396CB2?logo=maplibre&logoColor=white)](https://maplibre.org/)
[![ESIP](https://img.shields.io/badge/ESIP-Federation-1B6CA8)](https://www.esipfed.org/)

---

Ask your AI assistant to *"draw the burn scar over Los Alamos and fly the camera to it"* — and watch it happen live in a browser tab. MapControl is a headless map server with a real-time MapLibre frontend: create maps, add GeoJSON and GeoTIFF overlays, animate the camera, switch basemaps and themes, take screenshots — over a **Python SDK**, a **REST API**, or the **Model Context Protocol** for Claude, Cline, and any other MCP client.

Think of it as the Star Trek computer's map console. You say the words; the map moves.

## Why MapControl?

- 🗺️ **Live & shareable** — every map is a URL. Open it anywhere; it updates in real time over WebSocket.
- 🤖 **Agent-native** — a first-class MCP server (Streamable HTTP) mounted at `/mcp`. Point Claude Desktop, Claude.ai, Cline, or the OpenAI Agents SDK at it and start talking to your map.
- 🐍 **Typed Python SDK** — the `mapcontrol` package gives you sessions, assets, styles, screenshots, and camera control in a few lines.
- 🌍 **Real geospatial data** — GeoJSON features, styled polygons/lines/points, glyph markers, arcs, and **GeoTIFF rasters** rendered server-side with colormaps.
- 🏔️ **3D terrain & globe** — MapLibre globe with terrain tiles and sky, or classic 2D. Ballistic zoom animations included.
- 📸 **Screenshots** — capture the current viewport to PNG, straight from a tool call.
- 🎨 **Basemaps & themes** — OpenStreetMap, Esri Satellite, and Carto Dark work with zero keys; drop in a MapTiler key for vector streets, hybrid, topo, and dataviz styles. Light/dark themes throughout.
- 🔐 **Optional auth** — self-issued signed scoped tokens for `/mcp`, plus a standalone OAuth-style authorization portal for multi-user deployments.

## Architecture

```
   Claude / Cline / any MCP client          Python SDK (`mapcontrol`)        curl / your app
                 │                                     │                           │
                 ▼                                     ▼                           ▼
            MCP (/mcp) ────────────────────────► REST API ◄───────────────────────┘
                                                     │
                                          FastAPI server (sessions,
                                          assets, events, GeoTIFFs,
                                          screenshots, auth)
                                                     │
                                              WebSocket fan-out
                                                     │
                                                     ▼
                                        MapLibre GL frontend (live map,
                                        3D terrain, drawing tools)
```

| Directory | What it is |
|---|---|
| [`server/`](server/) | FastAPI server — REST API, WebSocket hub, MCP server, GeoTIFF & screenshot services, auth portal |
| [`sdk/`](sdk/) | `mapcontrol` — typed Python client SDK |
| [`examples/`](examples/) | Runnable demo scripts (shapes, terrain, glyphs, GeoTIFFs) + sample data |
| [`docs/`](docs/) | Guides — MCP integration, MCP Apps field guide, map-engine comparison |
| [`docs/vision/`](docs/vision/) | Design docs — architecture, specification, roadmap, protocol notes |
| [`deploy/`](deploy/) | Deployment helpers (local PyPI index for the SDK) |

## Quick start

### 1. Run the server

**Docker (fastest):**

```bash
docker compose up --build
```

**Or from source:**

```bash
cd server
pip install -e ".[dev]"
uvicorn mapcontrol_server.main:app --reload --port 8000
```

The server is at `http://localhost:8000` — interactive API docs at `/docs`.

### 2. Drive a map from Python

```bash
cd sdk && pip install -e .
```

```python
from mapcontrol import MapControl

mc = MapControl("http://localhost:8000")
session = mc.create_map(name="demo")
print(session.map_url)   # ← open this in a browser; it updates live

session.zoom_to(-122.42, 37.77, zoom=12)         # fly to San Francisco
session.add_polygon([[-122.5, 37.7], [-122.4, 37.7], [-122.4, 37.8]])
session.set_basemap("satellite")
session.set_theme("dark")

shot = session.take_screenshot()                  # PNG of the current view
```

### 3. Connect an AI assistant (MCP)

The MCP server is mounted **in-process** at `/mcp` (Streamable HTTP — the current MCP standard). Add it to any MCP client:

**Cline / Claude Desktop** (`mcpServers` config):

```json
{
  "mcpServers": {
    "mapcontrol": {
      "url": "http://localhost:8000/mcp"
    }
  }
}
```

Then just ask:

> *"Create a map, add a red polygon around Austin, switch to satellite, and zoom to it."*

See **[docs/mcp-integration.md](docs/mcp-integration.md)** for full setup guides (Claude Desktop, Cline, Claude.ai web, OpenAI Agents SDK) and transport details.

## MCP tools

| Tool | What it does |
|---|---|
| `create_map` | Create a live map and get its shareable URL |
| `add_geojson` | Add styled GeoJSON features (polygons, lines, points, glyphs) |
| `add_arc` | Add a great-circle arc between two points |
| `add_geotiff` | Upload & render a GeoTIFF raster with a colormap |
| `zoom` | Fly the camera — to coordinates, bounds, or assets (ballistic animation) |
| `list_assets` | List everything on a map |
| `update_style` | Restyle an existing asset |
| `set_visibility` | Show / hide an asset |
| `delete_asset` | Remove an asset |
| `set_basemap` | Switch basemaps (OSM, satellite, dark, MapTiler styles…) |
| `set_theme` | Light / dark theme |
| `get_viewport` | Read the current camera position |
| `take_screenshot` | Capture the current view to PNG |

## REST API

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/maps` | Create a map |
| `GET` | `/api/maps/{id}` | Get map info |
| `DELETE` | `/api/maps/{id}` | Delete a map |
| `POST` | `/api/maps/{id}/sessions` | Create a user session |
| `POST` | `/api/maps/{id}/events` | Send a map command |
| `GET` | `/api/maps/{id}/events` | List events |
| `GET` | `/api/maps/{id}/assets` | List assets |
| `PATCH` | `/api/maps/{id}/assets/{aid}` | Update an asset |
| `DELETE` | `/api/maps/{id}/assets/{aid}` | Delete an asset |
| `GET` | `/map/{id}` | Map frontend (iframe-ready; `?ui=none` for a chromeless embed) |
| `WS` | `/ws/{id}/{session}` | WebSocket for real-time events |

## Configuration

Everything lives in [`server/config.toml`](server/config.toml) — host/port, session TTL, storage paths, default basemap/theme/terrain, and the full basemap catalog. Keyless basemaps (OSM, Esri Satellite, Carto Dark) work out of the box; set `MAPTILER_API_KEY` to unlock the MapTiler vector styles. Adding a new tile provider is a config-only change.

Optional auth for `/mcp` is controlled via environment (`MAPCONTROL_AUTH_MODE`) — see [`server/mapcontrol_server/portal/`](server/mapcontrol_server/portal/) for the standalone authorization portal.

## Demos

Runnable scripts in [`examples/`](examples/) show the SDK end to end (start the server first):

```bash
python examples/demo.py                    # tour: shapes, styles, zoom, basemaps
python examples/demo_terrain_showcase.py   # 3D terrain + ballistic camera moves
python examples/demo_glyphs.py             # glyph markers & labels
# see also: demo_drawing.py, demo_ballistic_zoom.py, demo_multi_session.py
```

Sample GeoTIFFs live in [`examples/data/`](examples/data/).

## Running tests

```bash
cd server
pip install -e ".[dev]"
pytest tests/ -v
```

## Community

Questions, ideas, demos to show off? **[Join us on Discord →](https://discord.gg/Q4BB4uF68B)**

MapControl is developed within the [ESIP Federation](https://www.esipfed.org/) community. Issues and pull requests are welcome.

## License

[Apache-2.0](LICENSE)
