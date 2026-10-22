# Deploying MapControl

MapControl ships as a single container: `ghcr.io/esipfed/mc2`. One image
serves the map pages, the REST API, and the MCP endpoint on one port.
This guide goes from the smallest thing that works to a full production
setup, and explains what every knob does to the map.

> **Not using Docker?** To run the server directly with Python from a source
> checkout, see **[local-development.md](local-development.md)**.

## 1. Minimal — the smallest thing that works

```yaml
# docker-compose.yml
services:
  mapcontrol:
    image: ghcr.io/esipfed/mc2:latest
    ports: ["8000:8000"]
```

```bash
docker compose up -d
curl http://localhost:8000/docs   # up?
```

You get: the live map UI, the REST API, and `/mcp` — all on port 8000.
You don't get: persistence (maps vanish when the container is removed),
working share links off this machine, or the premium basemaps.

## 2. Recommended — persistence + shareable links

```yaml
services:
  mapcontrol:
    image: ghcr.io/esipfed/mc2:latest
    ports: ["8000:8000"]
    volumes:
      - ./data:/app/data                          # maps survive restarts
    environment:
      MAPCONTROL_DB_PATH: /app/data/mapcontrol.db
      MAPCONTROL_FILE_DIR: /app/data/files
      MAPCONTROL_PUBLIC_URL: https://maps.example.org   # baked into share links
    restart: unless-stopped
```

The `./data` volume holds the SQLite database (maps, assets, the event
log) and every uploaded GeoTIFF. Remove it and your maps die with the
container.

`MAPCONTROL_PUBLIC_URL` is the origin written into every shareable map
link and screenshot URL. Leave it unset and links point at `localhost`
— fine on your laptop, broken for anyone else.

## 3. Full — premium basemaps, pinned version, custom config

```yaml
services:
  mapcontrol:
    image: ghcr.io/esipfed/mc2:sha-2920da5     # pin a digest for deployments
    ports: ["8000:8000"]
    volumes:
      - ./data:/app/data
      - ./config.toml:/app/server/config.toml:ro   # your own map defaults
    environment:
      MAPCONTROL_DB_PATH: /app/data/mapcontrol.db
      MAPCONTROL_FILE_DIR: /app/data/files
      MAPCONTROL_PUBLIC_URL: https://maps.example.org
      MAPTILER_API_KEY: ${MAPTILER_API_KEY}        # unlocks MapTiler basemaps
    restart: unless-stopped
```

Image tags: `latest` tracks `main`, `sha-<short>` pins an exact build,
and `vX.Y.Z` tags appear on releases.

## What each knob does to the map

| Variable | Effect on the map |
|---|---|
| `MAPCONTROL_PUBLIC_URL` | The origin baked into every shared map link, session URL, and screenshot reference. Set it to whatever your users type in a browser. |
| `MAPTILER_API_KEY` | Adds the MapTiler basemaps (vector streets, hybrid, topo, dataviz, satellite-dark) to the basemap picker and to `set_basemap`. Without it you still get the keyless three: OSM, Esri Satellite, Carto Dark. |
| `MAPCONTROL_DB_PATH` | Where the SQLite database lives — maps, assets, the full event log that replays when someone opens a map URL. |
| `MAPCONTROL_FILE_DIR` | Where uploaded GeoTIFFs and rendered raster tiles are stored. |
| `MAPCONTROL_PORT` / `MAPCONTROL_HOST` | Bind address inside the container (default `0.0.0.0:8000`). Usually you change the compose port mapping instead. |
| `MAPCONTROL_ROOT_PATH` | Serve under a path prefix (e.g. `/maps`) behind a reverse proxy. Rewrites every route and generated link accordingly. |
| `MAPCONTROL_CONFIG_PATH` | Point at an alternative `config.toml`. |
| `MAPCONTROL_AUTH_MODE` | Turns on authorization for `/mcp` (`standalone` runs the built-in portal). Off by default — anyone who can reach the server can drive maps. |
| `MAPCONTROL_MCP_AUTH*` | The MCP token knobs (secret, issuer, scopes, TTLs) — see the auth portal code in `server/mapcontrol_server/portal/`. |

## config.toml — map defaults and the basemap catalog

Mount your own copy read-only (`./config.toml:/app/server/config.toml:ro`)
to change what every new map looks like:

- `default_basemap`, `default_dark_basemap` — what maps open with
- `default_terrain` — `"3d"` (globe + terrain + sky) or `"2d"`
- `[session] ttl_seconds` — how long inactive maps live (0 = forever)
- `[map.defaults]` — starting center/zoom
- `[map.basemaps.*]` — the full basemap catalog. Adding your own tile
  provider is a config-only change: add a `[map.providers.X]` block
  naming the API-key env var, then one `[map.basemaps.Y]` block per
  style referencing it.

Grab the stock file as a starting point:
[`server/config.toml`](../server/config.toml).

## Behind a reverse proxy

Point your proxy at port 8000 and make sure **WebSocket upgrades pass
through** — the live map updates ride `/ws/...`. Set
`MAPCONTROL_PUBLIC_URL` to the proxy's public origin.

```caddyfile
maps.example.org {
    reverse_proxy mapcontrol:8000
}
```

(Caddy proxies websockets automatically; for nginx add the usual
`Upgrade`/`Connection` headers on `location /ws/`.)

Serving under a subpath instead of a subdomain? Set
`MAPCONTROL_ROOT_PATH=/maps` and route `example.org/maps/*` to the
container.

## Building from source instead

Contributors working on the server itself can swap the image line for a
local build:

```yaml
    build: .        # instead of image: ghcr.io/esipfed/mc2:latest
```

Then `docker compose up --build` from a clone of this repo.
