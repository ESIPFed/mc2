# Running MapControl without Docker (from source)

This guide runs the server directly with Python — no Docker. It's the setup you
want for local development, debugging, or hosts where Docker isn't available.

Everything the Docker image does, it does from these same steps: the image is
just Python + these dependencies + a headless Chromium.

---

## Prerequisites

- **Python ≥ 3.11** (3.11 or 3.12). Check: `python3 --version`
- **git**
- No system GDAL/GEOS needed — the `rasterio` wheels bundle their own on
  Linux, macOS, and Windows.
- No Node.js and no frontend build needed — the server serves the map page
  with MapLibre loaded from a CDN. (Node is only needed for the optional
  [Puppeteer animation skills](puppeteer-skills/).)

---

## 1. Clone

```bash
git clone https://github.com/ESIPFed/mc2.git
cd mc2
```

## 2. Create and activate a virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
python -m pip install --upgrade pip
```

## 3. Install the server

```bash
pip install -e ./server
```

This pulls FastAPI, uvicorn, rasterio/numpy/matplotlib (GeoTIFF rendering),
Pillow, the MCP SDK, and Playwright.

## 4. Install the screenshot browser (Playwright Chromium)

The `take_screenshot` feature renders pages with a headless Chromium. Install it
once — **skip this and everything works except screenshots** (they return a
clear "install chromium" error):

```bash
playwright install chromium
```

## 5. (Optional) Install the Python SDK

Only if you want to drive the map from Python (`from mapcontrol import
MapControl`):

```bash
pip install -e ./sdk
```

---

## 6. Run the server

Run it on **port 8000** to match the rest of the docs. Setting
`MAPCONTROL_PORT` matters — see [Why set the port](#why-set-the-port) below.

From the repo root, with the venv active:

```bash
cd server
MAPCONTROL_PORT=8000 uvicorn mapcontrol_server.main:app --host 0.0.0.0 --port 8000
```

> **Windows (PowerShell):**
> ```powershell
> cd server
> $env:MAPCONTROL_PORT=8000; uvicorn mapcontrol_server.main:app --host 0.0.0.0 --port 8000
> ```

**Auto-reload for development** — add `--reload`, or use the console script,
which turns reload on for you:

```bash
cd server
MAPCONTROL_PORT=8000 mapcontrol-server
```

The SQLite database and uploads directory are created automatically under
`./data/` (relative to the directory you launch from — hence `cd server`, so
they land in `server/data/`, matching the container layout). To put them
elsewhere, set `MAPCONTROL_DB_PATH` and `MAPCONTROL_FILE_DIR`.

## 7. Verify

The first start takes ~20–30s while Python imports the geospatial stack — wait
for the `Uvicorn running on http://…:8000` line before hitting it.

```bash
curl http://localhost:8000/docs      # interactive API docs → 200
```

Create a map and open it live:

```bash
# create a map, print its id
curl -s -X POST http://localhost:8000/api/maps \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['map_id'])"
```

Open `http://localhost:8000/map/<MAP_ID>` in a browser. Or drive it from Python
(if you installed the SDK):

```python
from mapcontrol import MapControl

mc = MapControl("http://localhost:8000")
session = mc.create_map()
print(session.url)                       # open this in a browser
session.zoom_to_point(7.6586, 45.9763, zoom=12)   # the Matterhorn
shot = session.take_screenshot()         # needs the Playwright browser (step 4)
```

MCP clients connect at `http://localhost:8000/mcp`.

---

## Why set the port

`config.toml` ships a development default of **7777**. The server reads its port
from `config.toml` unless you override it — but uvicorn's own `--port` flag only
controls what it *binds*, not what the app *thinks* it's on. If those two
disagree, headless screenshots break: the server self-navigates Chromium to
`http://127.0.0.1:<config port>`, which won't be listening.

Setting **`MAPCONTROL_PORT=8000`** fixes both at once — `config.toml` is
overridden to 8000 and uvicorn binds 8000 — so the bound port, the app's idea of
its port, and the URLs in these docs all agree. (This is exactly what the Docker
image does via `ENV MAPCONTROL_PORT=8000`.)

If you'd rather just use the built-in default, run `mapcontrol-server` with **no**
`MAPCONTROL_PORT` and reach the server at `http://localhost:7777` — bound and
config agree, and screenshots work — but remember to swap `8000 → 7777` in every
command above.

## Optional: premium basemaps

The keyless basemaps (OpenStreetMap, Esri Satellite, Carto Dark) work out of the
box. To add the MapTiler styles (vector streets, hybrid, topo, dataviz,
satellite-dark), export a key before launching:

```bash
export MAPTILER_API_KEY=your_key_here
```

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `take_screenshot` fails with a browser/executable error | Run `playwright install chromium` (step 4), in the same venv. |
| Screenshots time out or hit a connection error, but the map loads in a browser | Port mismatch — launch with `MAPCONTROL_PORT` set so the bound port matches config (see [Why set the port](#why-set-the-port)). |
| `Address already in use` on 8000 | Something else holds the port (`lsof -i :8000`). Pick another: `MAPCONTROL_PORT=8010 uvicorn ... --port 8010`. |
| `ModuleNotFoundError: No module named 'mcp'` | The venv isn't active, or `pip install -e ./server` didn't run. Re-activate and reinstall. |
| Share links / screenshot URLs point at `localhost` from another machine | Set `MAPCONTROL_PUBLIC_URL` to the address clients use, e.g. `export MAPCONTROL_PUBLIC_URL=http://192.168.1.50:8000`. Keep the container/host reachable on the bound port. |
| `rasterio` fails to install | Upgrade pip (`pip install --upgrade pip`) so it fetches a prebuilt wheel; make sure Python is 3.11/3.12. |
