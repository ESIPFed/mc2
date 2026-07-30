# Roadmap & Next Steps

**Last updated:** 2026-02-28
**Current state:** MVP core working — server, SDK, WebSocket events, session management, per-session routing, viewport restore, demos passing.

---

## What's Done (MVP Core)

| Feature | Status | Notes |
|---------|--------|-------|
| FastAPI proxy server | ✅ | REST API + WebSocket |
| SQLite persistence | ✅ | Sessions, assets, events |
| Python SDK (`mapcontrol`) | ✅ | Full API: create_map, add_polygon, zoom, etc. |
| WebSocket real-time events | ✅ | Broadcast for shared events, per-session for personal |
| Session management | ✅ | map_id + user_session_id model |
| Per-session zoom/basemap | ✅ | Zoom only affects the requesting session |
| Session restore | ✅ | Existing sessions restore exact viewport; new sessions zoom-to-fit assets |
| Add/delete polygons & paths | ✅ | Via GeoJSON string |
| Asset visibility toggle | ✅ | Per-asset show/hide |
| Style updates | ✅ | Fill, stroke, width |
| Basemap toggle | ✅ | OSM + ESRI satellite |
| Zoom operations | ✅ | To point, to bbox, to assets, zoom out |
| Inline OpenLayers frontend | ✅ | CDN fallback — works without a build step |
| Docker setup | ✅ | Dockerfile + docker-compose.yml (basic) |
| 13 passing tests | ✅ | Maps, events, assets |
| Demo scripts | ✅ | Single-session + multi-session |

---

## Phase 1: MVP Completion

These items round out the MVP to match the original "done" scenario.

### 1.1 MCP Server
**Priority:** 🔴 High — this is the gateway to the "Star Trek computer" demo
**Effort:** Medium (1-2 days)

Build an MCP server that wraps the Python SDK, exposing tools like:
- `create_map()` → returns map_id + URL
- `add_polygon(map_id, geojson, style)` → adds a polygon
- `zoom_to_point(map_id, lon, lat, zoom)` → animates zoom
- `list_assets(map_id)` → returns all assets
- `set_basemap(map_id, basemap)` → switches basemap
- etc.

This enables the demo: *"I open Claude, use the MCP to create a map, get a URL, open it, then tell Claude to add three polygons and zoom to them."*

**Implementation:** Use the MCP SDK (Python). The MCP server wraps `MapControl` and `MapSession` from our SDK. Each Claude conversation creates one map session.

### 1.2 CLI Interface
**Priority:** 🟡 Medium
**Effort:** Small (half day)

A CLI wrapping the Python SDK for terminal/script usage:
```bash
mapcontrol create-map --server http://localhost:8888
mapcontrol add-polygon --map-id abc123 --geojson '...' --style '{"fill_color":"#ff0000"}'
mapcontrol zoom-to-point --map-id abc123 --lon -97.7 --lat 30.2 --zoom 14
mapcontrol list-assets --map-id abc123
```

Could use `click` or `typer`. Wraps the SDK — minimal new code.

### 1.3 URL-Based Asset Loading
**Priority:** 🟡 Medium — already in MVP scope per decisions
**Effort:** Small (already implemented server-side, needs demo/testing)

`add_polygon_url` and `add_path_url` are implemented but not exercised in demos. Should test with real hosted GeoJSON files.

### 1.4 Proper Frontend Build
**Priority:** 🟡 Medium
**Effort:** Medium (1 day)

Replace the inline CDN frontend with the planned `frontend/` Vite + TypeScript project:
- Proper module structure (event router, handlers, state management)
- Hot reload during development
- Minified production build served by FastAPI
- Easier to extend with new features (drawing, popups, etc.)

The inline version works fine for now, but becomes hard to maintain as we add features.

---

## Phase 1.5: High-Value Additions

### 1.5.1 Screenshot / Export
**Priority:** 🟡 Medium — great for AI integration
**Effort:** Medium (1 day)

Capture the current map view as a PNG image and host it:
- API: `POST /api/maps/{map_id}/screenshot` → returns `{ url: "http://...screenshot.png" }`
- Server uses headless Chromium (Playwright) to render the map at the session's current viewport
- Stored in local file storage, served at a static URL
- Enables: *"Show me what the map looks like"* from an AI agent

### 1.5.2 Drawing Tools
**Priority:** 🟡 Medium
**Effort:** Medium (1-2 days)

Let users draw polygons/lines/points directly on the map:
- Frontend: OpenLayers `ol.interaction.Draw`
- On draw complete → send `user_drawn_feature` via WebSocket → stored in server
- Queryable via `GET /api/maps/{map_id}/drawn-features` or included in asset list
- Enables: *"What did the user draw?"* from an AI agent

### 1.5.3 Interaction Events (postMessage + window CustomEvents)  ✅ Implemented
**Priority:** 🟡 Medium — enables embedding applications to react to user actions

The map emits user interaction events so embedding applications (or the optional
default outfit) can handle clicks, hovers, etc. however they want. No built-in
popups or panels in the naked map — it stays pure.

Implemented in `server/mapcontrol_server/static/esip-contract.js` — an
ALWAYS-loaded interaction contract (naked *and* dressed). It emits each event on
two channels: a same-document `window` CustomEvent (`esip:asset_click`, …) for
in-page consumers, and `window.parent.postMessage(...)` for iframe-embedding
parents (e.g. the Svelte app).

Events:
- `asset_click` — user clicks an asset (asset_id, coords)
- `asset_hover` / `asset_hover_end` — mouse enter/leave (asset_id, name, type, point, lngLat)
- `map_click` — click on empty space (lon, lat)
- `draw_complete` — user finishes drawing (geojson)

The contract also publishes `window.ESIPMap` — a public in-page command surface
a vibe-coder builds their own UI against. Its action methods POST to the public
REST `/events` endpoint exactly like the Python SDK, so the built-in UI and any
third-party UI drive the map through the identical contract.

**Design decision:** No built-in click behavior. The embedding application owns
the UX. Different apps will do different things with a click — open a pane,
highlight in a list, tell an AI. We just emit the event.

### 1.5.3b Naked vs. Default UI ("the outfit")  ✅ Implemented
**Priority:** 🟡 Medium

The served map now has two presentation modes, selected by a `?ui=none|default`
query param (falling back to `config.map.default_ui`, default `none`):

- **Naked (`?ui=none`)** — a bare, chrome-less canvas. Only the always-on
  interaction contract loads; the map emits events into the void. This is the
  canonical ESIP design and the surface third parties vibe-code against.
- **Default outfit (`?ui=default`)** — additionally loads `esip-embed.css/js`,
  the reference consumer: a grouped layer panel (Analysis / Drawn / Basemap),
  hover cards (visual-left / text-right, auto-generated from style + geometry
  stats when a producer sets no `metadata.card_md`/`thumbnail`), and a settings
  tab. Built *entirely* on the public event/REST contract — shipping it dogfoods
  that the contract is good enough to build a real UI on.

Static assets are served at `/static/*`; the inline map shell publishes
`window.__esipInternals` so the contract can wire MapLibre events while still
driving all state changes through the public REST API.

### 1.5.4 Layer Ordering / Z-Index
**Priority:** 🟢 Low
**Effort:** Small

Control the stacking order of overlapping assets. API: `PATCH /api/maps/{map_id}/assets/{id}` with a `z_index` field.

---

## Phase 2: Advanced Features

### 2.1 GeoTIFF Support
**Effort:** Medium-Large

Add raster imagery to the map:
- For small files: direct loading via OpenLayers' GeoTIFF source
- For large files: COG + TiTiler tile server
- API: `add_geotiff_url(url)`, `add_geotiff_file(bytes)`
- Docker Compose adds a TiTiler container

### 2.2 ARC Visualization
**Effort:** Medium

For DeckGL phase — arcs between two points with color and animation. Requires DeckGL integration.

### 2.3 Animated Polygons / Paths
**Effort:** Medium

Glowing, pulsing, or animated assets using OpenLayers animations or CSS:
- `animated: true` flag already exists in the data model
- Need to implement the actual animation rendering in the frontend

### 2.4 Narrative Mode
**Effort:** Large

Scripted sequences of map movements, style changes, and data reveals:
- JSON/YAML definition format
- Python API to generate narratives programmatically
- Frontend playback engine
- Linear sequences first; branching later

### 2.5 Leaflet / DeckGL Providers
**Effort:** Large (per provider)

Support alternative map rendering libraries:
- Each provider is a separate frontend implementation
- Server selectable at startup: `--provider openlayers|leaflet|deckgl`
- Share event handling; differ in rendering

### 2.6 Temporal Data / Time Slider
**Effort:** Medium-Large

Assets with time ranges, playback controls, time-based filtering.

### 2.7 Heatmaps
**Effort:** Medium

DeckGL-based heatmap visualization from point data. Deferred until DeckGL provider is built.

---

## Infrastructure & Quality

### Docker Compose Hardening
- Multi-stage Dockerfile building frontend + server
- Volume mounts for persistent data
- Health checks
- Optional TiTiler sidecar for GeoTIFF serving

### Testing Improvements
- SDK integration tests (against a real running server)
- WebSocket tests (session restore, viewport reporting)
- Frontend tests (if/when we build the Vite frontend)
- Load testing (many concurrent sessions on one map)

### Configuration
- TOML config for non-secrets ✅
- Environment variables for API keys/secrets ✅
- Add: configurable CORS origins for production
- Add: configurable session TTL cleanup job

### Database Migration Strategy
- Currently using `CREATE TABLE IF NOT EXISTS` (fine for now)
- When schema changes: add a simple migration system (numbered SQL files)
- Eventually: switch to PostgreSQL + PostGIS for spatial queries

---

## Suggested Order of Work

Based on impact and dependencies:

```
1. MCP Server               ← enables the "done" demo scenario
2. Clean up DB/sessions      ← quality of life for development
3. Screenshot/Export         ← high-value AI integration feature
4. CLI Interface             ← quick win, wraps SDK
5. Proper Frontend Build     ← foundation for drawing, popups, etc.
6. Drawing Tools             ← interactive feature, needs frontend
7. Asset Popups              ← uses existing metadata
8. GeoTIFF Support           ← opens up raster workflows
9. Animated Assets           ← visual polish
10. Narrative Mode           ← advanced, needs design
```

---

## Known Issues / Tech Debt

| Issue | Severity | Notes |
|-------|----------|-------|
| SQLite DB persists across server restarts | Low | Old maps accumulate. Add a cleanup endpoint or TTL job. |
| No input validation on GeoJSON | Medium | Malformed GeoJSON could crash the frontend. Should validate server-side. |
| No authentication | Low (for now) | Anyone can control any map. Fine for local dev. |
| Inline frontend is a giant f-string | Medium | Works but hard to maintain. Proper frontend build fixes this. |
| No favicon | Cosmetic | Server returns 404 for /favicon.ico. Add one. |
| Demo scripts hardcode port 8888 | Low | Should read from env or config. |
| No rate limiting | Low | Could matter if exposed publicly. |
