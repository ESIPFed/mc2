# Decisions Log

All questions have been resolved. This document records the final decisions.

---

## 1. Architecture & Infrastructure

| Decision | Answer |
|----------|--------|
| **1.1 Proxy Server** | FastAPI |
| **1.2 Event Delivery** | WebSockets |
| **1.3 Persistence** | SQLite |
| **1.4 File/Asset Storage** | Local filesystem (S3 later) |
| **1.5 GeoTIFF Serving** | Direct loading via OpenLayers for small files. COG + tile server deferred until needed. |

## 2. Session & Multi-User Model

| Decision | Answer |
|----------|--------|
| **2.1 Map ID vs User Session** | `map_id` = shared workspace, `user_session_id` = individual viewer. Viewport and basemap selection are per-user. |
| **2.2 Session Lifecycle** | Explicit `create_map()` → returns `map_id` + URL. TTL-based cleanup (configurable). |
| **2.3 Session Restoration** | State snapshot (all assets + last viewport). No event replay. Frontend reports viewport changes back to server. |
| **2.4 Conflict Resolution** | Last-write-wins. Timestamp-ordered events. |

## 3. Map Provider & Frontend

| Decision | Answer |
|----------|--------|
| **3.1 Multi-Provider Strategy** | Option B — Build for OpenLayers first, refactor toward abstraction later. Keep event handling separate from rendering. |
| **3.2 Frontend Tooling** | Vanilla TypeScript + Vite. No framework. |
| **3.3 Animations** | OpenLayers-native animations + CSS. Animation is always optional per asset. |
| **3.4 Basemaps** | OSM (default) + ESRI World Imagery (satellite). Toggle via API (`set_basemap`). Configurable list. |

## 4. API Design

| Decision | Answer |
|----------|--------|
| **4.1 Asset Identity** | Auto-generated UUID + optional user-provided `name`. Both usable in API calls. |
| **4.2 Event Granularity** | Atomic events. One action = one event. Batch endpoint for compound operations. |
| **4.3 Styling** | Style object in API call with sensible defaults. Named presets. GeoJSON properties can override. |
| **4.4 Metadata** | Fixed top-level fields (title, description) + freeform `metadata` dict. |
| **4.5 Error Handling** | Synchronous HTTP response confirms event queued. |
| **4.6 Drawing** | Frontend auto-sends drawn features to proxy on draw completion. Queryable via `get_user_drawn_assets()`. |

## 5. Narrative Mode

| Decision | Answer |
|----------|--------|
| **5.1 Format** | JSON/YAML script. Python API can generate. |
| **5.2 Branching** | Deferred entirely for now. |

## 6. Scope & Prioritization

### MVP (Phase 1)
- Proxy server with session management
- OpenLayers frontend with WebSocket event consumption
- Python API library (core interface)
- Zoom operations (point, polygon, assets, out)
- Add/delete vector assets (polygon, path via GeoJSON string)
- Add vector assets via URL
- Asset visibility toggle
- List assets
- Basic styling (color, opacity)
- Session persistence (restore state on reload)
- Basemap toggle (OSM + satellite)

### Phase 1.5
- Screenshot/export (capture map as PNG, host it, return URL)

### Deferred
- GeoTIFF support
- ARC visualization
- Narrative Mode
- CLI interface
- MCP interface
- Leaflet/DeckGL providers
- Asset manager component
- Drawing tools
- Animated polygons/paths
- Geocoding (external tool responsibility)
- Heatmaps
- Time slider

### "Done" for Phase 1
"I open Claude, use the MCP to create a map, get a URL, open it, then tell Claude to add three polygons and zoom to them—and I see it happen live with smooth animations."

## 7. Deployment & Operations

| Decision | Answer |
|----------|--------|
| **7.1 Deployment** | Docker / Docker Compose. No tile server for now. |
| **7.2 Configuration** | TOML config file for non-secret settings. Environment variables for API keys/secrets. |

## 8. Additional Features

| Feature | Status |
|---------|--------|
| Screenshot/export | Phase 1.5 ✅ |
| Geocoding | Out of scope (external tool) |
| Heatmaps | Deferred (DeckGL phase) |
| Time slider | Deferred |
| Layer ordering | TBD |
| Clustering | TBD |
| Measurement tools | TBD |
| Coordinate display | TBD |
| Scale bar | TBD |

## 9. Development Approach

- Minimal testing — Python API tests at minimum
- No CI/CD for now

## 10. User Interaction Events

| Decision | Answer |
|----------|--------|
| **10.1 Asset click behavior** | No built-in popup or panel. Map emits `postMessage` events to the parent window. Embedding app decides what to do. |
| **10.2 Event delivery** | Dual channel: `window.parent.postMessage()` for iframe embedding + WebSocket → Server for AI agent access. |
| **10.3 Event types** | `asset_click`, `asset_hover`, `asset_hover_end`, `map_click`, `viewport_changed`, `draw_complete` |
| **10.4 AI interaction query** | `GET /api/maps/{id}/sessions/{sid}/interactions` — lets AI ask "what did the user click?" |
| **10.5 Rationale** | Different embedding apps want different behaviors. One opens a pane, another highlights a list, an AI uses it as context. The map should not presume. |
