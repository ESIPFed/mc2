# Technical Architecture

This document describes the system architecture for the Map Control project (Phase 1 MVP).

---

## System Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                        CONSUMERS                                │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────┐   │
│  │ Python   │  │   CLI    │  │   MCP    │  │  Any HTTP    │   │
│  │ Library  │  │ (later)  │  │ (later)  │  │  Client      │   │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └──────┬───────┘   │
│       │              │             │               │            │
│       └──────────────┴─────────────┴───────────────┘            │
│                          HTTP REST API                          │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────────────┐
│                     PROXY SERVER (FastAPI)                        │
│                                                                  │
│  ┌─────────────┐  ┌─────────────┐  ┌──────────────────────────┐ │
│  │   REST API  │  │  WebSocket  │  │   Session Manager        │ │
│  │  Endpoints  │  │  Manager    │  │   (map_id + user_session) │ │
│  └──────┬──────┘  └──────┬──────┘  └──────────┬───────────────┘ │
│         │                │                     │                 │
│  ┌──────┴────────────────┴─────────────────────┴───────────────┐ │
│  │                    Event Bus                                │ │
│  │  API call → Event created → Persisted → Broadcast via WS   │ │
│  └──────────────────────┬──────────────────────────────────────┘ │
│                         │                                        │
│  ┌──────────────────────┴──────────────────────────────────────┐ │
│  │                   Storage Layer                             │ │
│  │  ┌──────────┐  ┌──────────────┐  ┌──────────────────────┐  │ │
│  │  │  SQLite  │  │  File Store  │  │  State Snapshots     │  │ │
│  │  │ (events, │  │  (uploaded   │  │  (viewport, layers,  │  │ │
│  │  │  assets, │  │   files)     │  │   visibility)        │  │ │
│  │  │  sessions│  │              │  │                      │  │ │
│  │  └──────────┘  └──────────────┘  └──────────────────────┘  │ │
│  └─────────────────────────────────────────────────────────────┘ │
│                                                                  │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │              Static File Server                             │ │
│  │  Serves the frontend app (Vite build output)               │ │
│  └─────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────┘
                           │
                    WebSocket + HTTP
                           │
                           ▼
┌──────────────────────────────────────────────────────────────────┐
│                    FRONTEND (OpenLayers + Vite)                   │
│                                                                  │
│  ┌─────────────┐  ┌─────────────┐  ┌──────────────────────────┐ │
│  │  WebSocket  │  │   Event     │  │   OpenLayers Map         │ │
│  │  Client     │──│   Router    │──│   ┌────────────────────┐ │ │
│  │             │  │             │  │   │ Layers / Sources   │ │ │
│  └─────────────┘  └─────────────┘  │   │ View / Animations  │ │ │
│                                    │   │ Interactions       │ │ │
│                                    │   └────────────────────┘ │ │
│                                    └──────────────────────────┘ │
│                                                                  │
│  URL: /map/{map_id}?user_session={user_session_id}              │
│  Rendered as iframe-friendly, pure map — no chrome.              │
└──────────────────────────────────────────────────────────────────┘
```

---

## Data Flow

### Command Flow (API → Map)
```
1. Consumer calls REST API:  POST /api/maps/{map_id}/events
   Body: { "type": "add_polygon", "data": { "geojson": "...", "style": {...} } }

2. Proxy validates, creates Event record in SQLite
   → Returns HTTP 200 + { "event_id": "...", "asset_id": "..." }

3. Proxy broadcasts event via WebSocket to all connected clients on that map_id

4. Frontend receives event → Event Router dispatches to handler
   → "add_polygon" handler: parses GeoJSON, applies style, adds to map layer

5. Map renders with smooth transition
```

### Viewport Reporting Flow (Map → Server)
```
1. User pans/zooms the map (or a zoom command animates)

2. On moveend/zoomend, frontend sends viewport state via WebSocket:
   { "type": "viewport_update", "bbox": [...], "zoom": 12, "center": [lon, lat] }

3. Proxy stores as latest viewport for this user_session_id
```

### Session Restore Flow
```
1. Browser loads /map/{map_id}?user_session={user_session_id}

2. Frontend connects WebSocket, sends { "type": "session_restore" }

3. Proxy responds with full state snapshot:
   {
     "assets": [ ... all current assets with styles ... ],
     "viewport": { "center": [lon, lat], "zoom": 12 },
     "basemap": "osm"
   }

4. Frontend hydrates: adds all assets (no animations), sets viewport, applies basemap
```

---

## API Design

### Session Management

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/maps` | Create a new map. Returns `{ map_id, url }` |
| `GET` | `/api/maps/{map_id}` | Get map info (created_at, asset count, etc.) |
| `DELETE` | `/api/maps/{map_id}` | Delete a map and all its data |
| `POST` | `/api/maps/{map_id}/sessions` | Create a user session for a map. Returns `{ user_session_id, url }` |

### Map Events (Commands)

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/maps/{map_id}/events` | Send a map event/command |
| `GET` | `/api/maps/{map_id}/events` | List events (with pagination) |

### Assets

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/maps/{map_id}/assets` | List all assets |
| `GET` | `/api/maps/{map_id}/assets/{asset_id}` | Get asset details |
| `DELETE` | `/api/maps/{map_id}/assets/{asset_id}` | Delete an asset |
| `PATCH` | `/api/maps/{map_id}/assets/{asset_id}` | Update asset (style, visibility, metadata) |

### Viewport

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/maps/{map_id}/sessions/{user_session_id}/viewport` | Get current viewport + contained assets |

### Frontend

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/map/{map_id}` | Serve the map frontend (accepts `?user_session=` query param) |
| `WS` | `/ws/{map_id}/{user_session_id}` | WebSocket connection for real-time events |

---

## Event Types

Each event has a `type` and a `data` payload. The frontend Event Router maps types to handlers.

### Zoom Events
```json
{ "type": "zoom_to_point", "data": { "lon": -97.7, "lat": 30.2, "zoom": 14 } }
{ "type": "zoom_to_bbox", "data": { "bbox": [minLon, minLat, maxLon, maxLat] } }
{ "type": "zoom_to_assets", "data": { "asset_ids": ["uuid1", "uuid2"] } }
{ "type": "zoom_out", "data": { "levels": 1 } }
```

### Asset Events
```json
{ "type": "add_polygon", "data": {
    "geojson": "...",
    "name": "Survey Area A",
    "style": { "fill_color": "#ff000066", "stroke_color": "#ff0000", "stroke_width": 2 },
    "metadata": { "title": "Survey Area", "description": "..." },
    "animated": false
}}

{ "type": "add_polygon_url", "data": {
    "url": "https://example.com/area.geojson",
    "name": "Remote Polygon",
    "style": { ... }
}}

{ "type": "add_path", "data": { "geojson": "...", "style": { ... } } }
{ "type": "add_path_url", "data": { "url": "...", "style": { ... } } }

{ "type": "delete_asset", "data": { "asset_id": "uuid" } }
{ "type": "set_visibility", "data": { "asset_id": "uuid", "visible": false } }
{ "type": "update_style", "data": { "asset_id": "uuid", "style": { ... } } }
```

### Map Events
```json
{ "type": "set_basemap", "data": { "basemap": "satellite" } }
```

### From Frontend → Server (via WebSocket)
```json
{ "type": "viewport_update", "data": { "bbox": [...], "center": [lon, lat], "zoom": 12 } }
{ "type": "user_drawn_feature", "data": { "geojson": "..." } }
```

---

## Database Schema (SQLite)

```sql
CREATE TABLE maps (
    id TEXT PRIMARY KEY,           -- UUID
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL,
    config TEXT                     -- JSON: basemap list, defaults, etc.
);

CREATE TABLE user_sessions (
    id TEXT PRIMARY KEY,            -- UUID
    map_id TEXT NOT NULL REFERENCES maps(id) ON DELETE CASCADE,
    created_at TIMESTAMP NOT NULL,
    last_active TIMESTAMP NOT NULL,
    viewport TEXT,                  -- JSON: { center, zoom, bbox }
    basemap TEXT DEFAULT 'osm'
);

CREATE TABLE assets (
    id TEXT PRIMARY KEY,            -- UUID
    map_id TEXT NOT NULL REFERENCES maps(id) ON DELETE CASCADE,
    name TEXT,                      -- Optional human-friendly name
    asset_type TEXT NOT NULL,       -- 'polygon', 'path', 'point', etc.
    geojson TEXT NOT NULL,          -- The actual geometry
    style TEXT,                     -- JSON style object
    metadata TEXT,                  -- JSON metadata (title, description, etc.)
    visible INTEGER DEFAULT 1,     -- Boolean
    animated INTEGER DEFAULT 0,    -- Boolean
    source_url TEXT,               -- If loaded from URL
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL
);

CREATE TABLE events (
    id TEXT PRIMARY KEY,            -- UUID
    map_id TEXT NOT NULL REFERENCES maps(id) ON DELETE CASCADE,
    user_session_id TEXT REFERENCES user_sessions(id),
    event_type TEXT NOT NULL,
    event_data TEXT NOT NULL,       -- JSON
    created_at TIMESTAMP NOT NULL
);

CREATE INDEX idx_assets_map ON assets(map_id);
CREATE INDEX idx_events_map ON events(map_id);
CREATE INDEX idx_sessions_map ON user_sessions(map_id);
```

---

## Python Library API

The Python library wraps REST calls to the proxy server.

```python
from mapcontrol import MapControl

# Connect to running proxy server
mc = MapControl(server_url="http://localhost:8000")

# Create a map
map_session = mc.create_map()
print(map_session.map_id)   # "abc-123-..."
print(map_session.url)      # "http://localhost:8000/map/abc-123-..."

# Add assets
polygon = map_session.add_polygon(
    geojson='{"type":"Feature","geometry":{"type":"Polygon","coordinates":[...]}}',
    name="Survey Area",
    style={"fill_color": "#ff000044", "stroke_color": "#ff0000"},
    metadata={"title": "Survey Area A", "description": "Initial survey boundary"}
)
print(polygon.asset_id)  # "def-456-..."

path = map_session.add_path_url(
    url="https://example.com/route.geojson",
    name="Supply Route"
)

# Zoom
map_session.zoom_to_point(lon=-97.74, lat=30.27, zoom=14)
map_session.zoom_to_assets([polygon.asset_id, path.asset_id])
map_session.zoom_out(levels=2)

# Manage
assets = map_session.list_assets()
map_session.set_visibility(polygon.asset_id, visible=False)
map_session.delete_asset(path.asset_id)

# Viewport
viewport = map_session.get_viewport(user_session_id="...")
print(viewport.bbox, viewport.contained_assets)

# Basemap
map_session.set_basemap("satellite")
```

---

## Project Structure

```
vibe-coding-experiment-map-control/
├── vision/                          # Project vision & decisions (existing)
│   ├── vision.md
│   ├── specification.md
│   ├── open-questions.md
│   └── architecture.md             # This document
│
├── server/                          # FastAPI proxy server
│   ├── pyproject.toml               # Python project config (dependencies, etc.)
│   ├── config.toml                  # Default server configuration
│   ├── mapcontrol_server/
│   │   ├── __init__.py
│   │   ├── main.py                  # FastAPI app, lifespan, static file serving
│   │   ├── config.py                # TOML config loader + env var overrides
│   │   ├── database.py              # SQLite setup, migrations, queries
│   │   ├── models.py                # Pydantic models (requests, responses, events)
│   │   ├── routers/
│   │   │   ├── __init__.py
│   │   │   ├── maps.py              # /api/maps endpoints
│   │   │   ├── events.py            # /api/maps/{id}/events endpoints
│   │   │   ├── assets.py            # /api/maps/{id}/assets endpoints
│   │   │   └── sessions.py          # /api/maps/{id}/sessions endpoints
│   │   ├── websocket.py             # WebSocket connection manager + event broadcast
│   │   ├── services/
│   │   │   ├── __init__.py
│   │   │   ├── session_service.py   # Session/map lifecycle logic
│   │   │   ├── asset_service.py     # Asset CRUD + URL fetching
│   │   │   └── event_service.py     # Event creation + dispatch
│   │   └── storage.py               # File storage abstraction (local, later S3)
│   └── tests/
│       ├── __init__.py
│       ├── test_maps.py
│       ├── test_assets.py
│       └── test_events.py
│
├── frontend/                        # OpenLayers map frontend
│   ├── package.json
│   ├── vite.config.ts
│   ├── tsconfig.json
│   ├── index.html                   # Entry point (minimal shell)
│   └── src/
│       ├── main.ts                  # Bootstrap: parse URL params, init map, connect WS
│       ├── map.ts                   # OpenLayers map setup (view, layers, controls)
│       ├── websocket.ts             # WebSocket client + reconnection logic
│       ├── event-router.ts          # Maps event types → handler functions
│       ├── handlers/
│       │   ├── zoom.ts              # Zoom event handlers (animated transitions)
│       │   ├── polygon.ts           # Add/remove polygon handlers
│       │   ├── path.ts              # Add/remove path handlers
│       │   ├── style.ts             # Style update handlers
│       │   ├── visibility.ts        # Visibility toggle handlers
│       │   └── basemap.ts           # Basemap switch handler
│       ├── state.ts                 # Client-side state (asset registry, current viewport)
│       ├── restore.ts               # Session restore logic (hydrate from snapshot)
│       ├── viewport-reporter.ts     # Reports viewport changes back to server
│       └── styles.css               # Minimal CSS (full-screen map, no chrome)
│
├── sdk/                             # Python client library
│   ├── pyproject.toml
│   ├── mapcontrol/
│   │   ├── __init__.py
│   │   ├── client.py                # MapControl class — top-level entry point
│   │   ├── session.py               # MapSession class — per-map operations
│   │   ├── models.py                # Data classes (Asset, Viewport, Style, etc.)
│   │   └── exceptions.py            # Custom exceptions
│   └── tests/
│       ├── __init__.py
│       └── test_client.py
│
├── docker-compose.yml               # Compose file: server (serves frontend build too)
├── Dockerfile                       # Multi-stage: build frontend + run server
├── .gitignore
├── LICENSE
└── README.md
```

---

## Build & Run

### Development
```bash
# Terminal 1: Frontend dev server (with hot reload)
cd frontend && npm install && npm run dev

# Terminal 2: Backend server
cd server && pip install -e . && uvicorn mapcontrol_server.main:app --reload

# The FastAPI server proxies to the Vite dev server in dev mode,
# or serves the built frontend in production mode.
```

### Production (Docker)
```bash
docker compose up --build
# Server runs on port 8000
# Map URLs: http://localhost:8000/map/{map_id}
# API: http://localhost:8000/api/...
# WS: ws://localhost:8000/ws/{map_id}/{user_session_id}
```

---

## User Interaction Events (postMessage Pattern)

The map frontend is designed to be embedded as an iframe. Different embedding applications will want to handle user interactions differently — one might open a side panel, another might highlight an item in a list, an AI agent might use it as context for the next decision.

**Design decision:** The map does NOT implement any built-in click behavior (no popups, no panels). Instead, it **emits interaction events** via `window.parent.postMessage()` and lets the embedding application decide what to do.

### Interaction Flow

```
User clicks an asset on the map (iframe)
    │
    ▼
Frontend detects click → identifies asset_id, gathers metadata
    │
    ├──► window.parent.postMessage({...})     → Embedding application handles it
    │                                            (side panel, list highlight, etc.)
    │
    └──► WebSocket → Server (optional)        → AI agent can query interactions
                                                 via REST API
```

### postMessage Event Types

The map iframe emits these events to `window.parent`:

| Event | Trigger | Payload |
|-------|---------|---------|
| `asset_click` | User clicks an asset | `{ asset_id, name, asset_type, metadata, click_coords: [lon, lat] }` |
| `asset_hover` | Mouse enters an asset | `{ asset_id, name, asset_type }` |
| `asset_hover_end` | Mouse leaves an asset | `{ asset_id }` |
| `map_click` | User clicks empty space | `{ lon, lat }` |
| `viewport_changed` | User pans/zooms | `{ center: [lon, lat], zoom, bbox }` |
| `draw_complete` | User finishes drawing | `{ geojson, geometry_type }` |

### postMessage Format

All messages follow the same envelope:

```javascript
window.parent.postMessage({
    source: "mapcontrol",           // Always "mapcontrol" — lets parent filter
    map_id: "abc-123",
    user_session_id: "def-456",
    type: "asset_click",            // Event type from table above
    data: {
        asset_id: "ghi-789",
        name: "Survey Area A",
        asset_type: "polygon",
        metadata: { title: "...", description: "..." },
        click_coords: [-97.74, 30.27]
    }
}, "*");
```

### Parent Application Listener (Example)

```javascript
window.addEventListener("message", (event) => {
    if (event.data?.source !== "mapcontrol") return;

    switch (event.data.type) {
        case "asset_click":
            // App-specific: open side panel, highlight in list, etc.
            console.log("User clicked:", event.data.data.name);
            break;
        case "viewport_changed":
            // Track what the user is looking at
            break;
    }
});
```

### Server-Side Interaction Channel (for AI Agents)

For AI agents that don't have a browser, interactions also flow through WebSocket → Server:

```
Frontend → WebSocket: { "type": "asset_click", "data": { "asset_id": "..." } }
Server stores in user_sessions or a dedicated interactions table
AI queries: GET /api/maps/{map_id}/sessions/{session_id}/interactions
```

This enables an AI to ask "what did the user just click?" without needing a browser.

### What the Map Does NOT Do

- ❌ No built-in popup on asset click
- ❌ No hardcoded "zoom to it" on click
- ❌ No side panel, no list, no chrome
- ✅ Emits the event and stays out of the way
- ✅ The embedding application decides the UX

---

## Key Design Principles

1. **The map is the product.** The frontend is a pure, chrome-less, full-viewport map designed to be embedded as an iframe. All controls come through the API.

2. **Events are the interface.** Every mutation flows through the event system. API → Event → Persist → Broadcast → Render. This makes the system auditable, replayable (if we want later), and naturally real-time.

3. **Animation is first-class.** Every zoom, every asset addition should feel smooth. We use OpenLayers' built-in animation capabilities. Speed is secondary to elegance.

4. **Restore, don't replay.** On reload, we hydrate from a state snapshot. No replaying animation history. The map should look correct immediately.

5. **AI-first API.** The Python library and REST API are designed to be called by AI agents. Clear, predictable, self-describing. Every response includes enough context for an agent to decide what to do next.
