# MCP Tool Consolidation Proposal

**Status:** Proposed (not yet implemented)  
**Date:** 2026-04-12

## Current State: 17 Tools

| # | Tool | Description |
|---|------|-------------|
| 1 | `create_map` | Create a new map and get a browser URL |
| 2 | `add_polygon` | Add a polygon from GeoJSON |
| 3 | `add_path` | Add a line/path from GeoJSON |
| 4 | `add_point` | Add a point marker from GeoJSON |
| 5 | `zoom_to_point` | Zoom to a lat/lon with animation |
| 6 | `zoom_to_assets` | Zoom to fit specific assets in view |
| 7 | `zoom_out` | Zoom out by N levels |
| 8 | `list_assets` | List all assets on the map |
| 9 | `delete_asset` | Remove an asset by ID |
| 10 | `set_visibility` | Show/hide one or more assets |
| 11 | `update_style` | Change fill color, stroke, width |
| 12 | `set_basemap` | Switch between OSM and satellite |
| 13 | `get_viewport` | Get current map center/zoom/bbox |
| 14 | `add_geotiff_rgb` | Add an RGB GeoTIFF raster overlay (via URL) |
| 15 | `add_geotiff_singleband` | Add a single-band GeoTIFF with colormap (via URL) |
| 16 | `upload_geotiff` | Upload a GeoTIFF file (base64 or local path fallback) |
| 17 | `take_screenshot` | Capture a PNG screenshot of the current map |

> Note: `docs/mcp-integration.md` says "13 tools" but is outdated — the 4 additions are `add_geotiff_rgb`, `add_geotiff_singleband`, `upload_geotiff`, and `take_screenshot`.

## Problem

17 tools is a lot of context injected into the LLM system prompt. Several tools are near-duplicates:
- `add_polygon`, `add_path`, `add_point` all do the same thing with different GeoJSON geometry types
- The 3 GeoTIFF tools overlap significantly
- The 3 zoom tools could be one tool with a target discriminator

## Proposed Consolidation: ~11 Tools

| Consolidated Tool | Replaces | Notes |
|---|---|---|
| `create_map` | (same) | No change |
| `add_geojson` | `add_polygon` + `add_path` + `add_point` | Server already handles all geometry types identically. One tool with `geojson` param covers all. |
| `add_geotiff` | `add_geotiff_rgb` + `add_geotiff_singleband` + `upload_geotiff` | Add `mode` param (`rgb`/`singleband`), accept `url` or `file_path`/`file_data` |
| `zoom` | `zoom_to_point` + `zoom_to_assets` + `zoom_out` | Discriminated union: `{target: "point", lon, lat, zoom}` or `{target: "assets", asset_ids}` or `{target: "out", levels}` |
| `list_assets` | (same) | No change |
| `delete_asset` | (same) | No change |
| `set_visibility` | (same) | Keep separate — clear single purpose |
| `update_style` | (same) | Keep separate — clear single purpose |
| `set_basemap` | (same) | No change |
| `get_viewport` | (same) | No change |
| `take_screenshot` | (same) | No change |

**Result: 11 tools** (down from 17)

## Design Principles

1. **Each tool should have a typed, bounded schema** — over-genericizing (e.g., `map_command(action, params)`) hurts LLM performance because the model hallucates action names and wrong param shapes.
2. **Merge tools that share identical schemas** — `add_polygon/add_path/add_point` all take `geojson` + `style`, so they should be one tool.
3. **Keep tools separate when schemas differ significantly** — `set_visibility` (boolean toggle) vs `update_style` (color/width params) are different enough to stay separate.
4. **Discriminated unions work for zoom** — the `target` field tells the LLM which param set to use.

## Caution

Do NOT create a single mega-tool like `map_command(action: string, params: object)` — this is terrible for LLM tool use because:
- The model has to guess valid action names
- The params schema is unbounded
- Error rates go way up
