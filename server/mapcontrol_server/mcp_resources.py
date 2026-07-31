"""MCP Resources layer for the Map Control server (roadmap §6, partial).

Phase 4+ §6 of ``.vision-documents/mcp-compliance-roadmap.md``: expose the server's
canonical state as **MCP Resources** with stable ``map://`` URIs, so MCP
clients (Claude web/desktop, Cline, ...) can read — and re-read — every
asset the tools create, in its most natural representation (GeoJSON as
``application/geo+json``, rendered rasters and screenshots as ``image/png``
binary blobs, everything else as JSON).

This module is **representation-only**: every resource is a thin *reader*
over the SAME service functions / files the REST API and tools already use.
No new state, no service behavior changes, no second schema (the Pydantic
models remain the source of truth — resources serialize them verbatim).

The resource taxonomy (all templates registered on the shared ``mcp_server``
instance from ``mcp_tools``):

    map://maps                                   application/json  (concrete)
    map://{map_id}                               application/json
    map://{map_id}/viewport                      application/json
    map://{map_id}/assets                        application/json  (index + per-asset resource_uri)
    map://{map_id}/assets/{asset_id}             application/geo+json
    map://{map_id}/assets/{asset_id}/style       application/json
    map://{map_id}/drawn                         application/geo+json (FeatureCollection)
    map://{map_id}/rasters/{asset_id}            image/png  (rendered GeoTIFF overlay)
    map://{map_id}/screenshots/{screenshot_id}   image/png

Design invariants (same as ``mcp_tools``):

* **EPSG:4326 only across the MCP boundary** — all GeoJSON out is lon/lat 4326
  (that is how the services store it; nothing re-projects here).
* **Explicit ``map_id``** in every URI (stateless-core ready).
* **Errors via the right channel** — unknown map / asset / file raises
  ``ValueError`` with a corrective message; FastMCP surfaces it as a resource
  read error the client can act on.
* Subscriptions / ``notifications/resources/updated`` are **deferred** —
  the advertised ``resources`` capability carries ``subscribe=False`` /
  ``listChanged=False`` so advertised == implemented still holds.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .config import load_config
from .models import AssetResponse
from .services import asset_service, session_service

# The shared FastMCP instance — resources register on the SAME server the
# tools live on, so /mcp serves both from one endpoint. The map:// URI helpers
# also live in mcp_tools (tools embed them in results / ResourceLinks); this
# import is one-way (mcp_resources -> mcp_tools), no cycle.
from .mcp_tools import (
    _public_base_url,
    asset_uri,
    map_uri,
    mcp_server,
    raster_uri,
    screenshot_uri,
)


# ─── Internal helpers ────────────────────────────────────────────────────────

# IDs are UUIDs today, but accept any filesystem-safe token so future ID
# schemes don't silently 404. Anything else (slashes, dots, ..) is rejected
# before touching the filesystem — same defense as routers/files.py.
_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]+$")


def _file_dir() -> Path:
    return Path(load_config().storage.file_dir)


def _safe_file(filename: str) -> Path:
    """Resolve a filename inside file_dir, refusing path escapes."""
    file_dir = _file_dir()
    path = (file_dir / filename)
    try:
        path.resolve().relative_to(file_dir.resolve())
    except ValueError:
        raise ValueError(f"Access denied: '{filename}' escapes the file store.")
    return path


async def _require_map(map_id: str) -> None:
    """Raise a corrective ValueError if the map does not exist."""
    info = await session_service.get_map(map_id)
    if info is None:
        raise ValueError(
            f"Map '{map_id}' not found. Call the create_map tool first, or read "
            "map://maps for the list of live maps."
        )


def _geojson_obj(asset: AssetResponse) -> dict[str, Any]:
    """The asset's GeoJSON as a dict (stored as a JSON string in the DB)."""
    gj = asset.geojson
    if isinstance(gj, str):
        return json.loads(gj)
    return gj


def _asset_index_entry(a: AssetResponse) -> dict[str, Any]:
    """One row of the map://{map_id}/assets index."""
    entry: dict[str, Any] = {
        "asset_id": a.asset_id,
        "name": a.name,
        "asset_type": a.asset_type,
        "visible": a.visible,
        "created_at": a.created_at,
        "resource_uri": asset_uri(a.map_id, a.asset_id),
        "style_uri": f"{asset_uri(a.map_id, a.asset_id)}/style",
    }
    # Rendered GeoTIFF overlays additionally expose their PNG blob.
    if a.asset_type.startswith("geotiff"):
        entry["raster_uri"] = raster_uri(a.map_id, a.asset_id)
    return entry


# ─── JSON resources ──────────────────────────────────────────────────────────

@mcp_server.resource(
    "map://maps",
    title="Live Maps",
    description="Index of all live map workspaces (map_id, browser URL, asset count).",
    mime_type="application/json",
)
async def res_maps() -> str:
    """List all live maps with their canonical resource URIs."""
    base = _public_base_url()
    maps = await session_service.list_maps()
    return json.dumps({
        "count": len(maps),
        "maps": [
            {
                "map_id": m.map_id,
                "resource_uri": map_uri(m.map_id),
                "url": f"{base}/map/{m.map_id}",
                "asset_count": m.asset_count,
                "created_at": m.created_at,
                "updated_at": m.updated_at,
            }
            for m in maps
        ],
    }, indent=2)


@mcp_server.resource(
    "map://{map_id}",
    title="Map Info",
    description="A map workspace: creation time, asset count, browser URL, and "
                "the URIs of its child resources (viewport, assets, drawn).",
    mime_type="application/json",
)
async def res_map(map_id: str) -> str:
    info = await session_service.get_map(map_id)
    if info is None:
        raise ValueError(f"Map '{map_id}' not found. Read map://maps for live maps.")
    base = _public_base_url()
    return json.dumps({
        "map_id": info.map_id,
        "url": f"{base}/map/{info.map_id}",
        "asset_count": info.asset_count,
        "created_at": info.created_at,
        "updated_at": info.updated_at,
        "viewport_uri": f"{map_uri(map_id)}/viewport",
        "assets_uri": f"{map_uri(map_id)}/assets",
        "drawn_uri": f"{map_uri(map_id)}/drawn",
    }, indent=2)


@mcp_server.resource(
    "map://{map_id}/viewport",
    title="Map Viewport",
    description="Current viewport (center/zoom/bbox, EPSG:4326), basemap, and terrain.",
    mime_type="application/json",
)
async def res_viewport(map_id: str) -> str:
    await _require_map(map_id)
    snapshot = await session_service.get_session_snapshot(map_id, "")
    vp = snapshot.viewport.model_dump(exclude_none=True) if snapshot.viewport else None
    return json.dumps({
        "map_id": map_id,
        "viewport": vp,
        "basemap": snapshot.basemap,
        "terrain": snapshot.terrain,
        "asset_count": len(snapshot.assets),
    }, indent=2)


@mcp_server.resource(
    "map://{map_id}/assets",
    title="Map Assets",
    description="Index of every asset on the map (id, name, type, visibility) with "
                "each asset's canonical resource URI for direct reads.",
    mime_type="application/json",
)
async def res_assets(map_id: str) -> str:
    await _require_map(map_id)
    assets = await asset_service.list_assets(map_id)
    return json.dumps({
        "map_id": map_id,
        "count": len(assets),
        "assets": [_asset_index_entry(a) for a in assets],
    }, indent=2)


@mcp_server.resource(
    "map://{map_id}/assets/{asset_id}/style",
    title="Asset Style",
    description="The asset's style (fill_color, stroke_color, stroke_width, line_dash).",
    mime_type="application/json",
)
async def res_asset_style(map_id: str, asset_id: str) -> str:
    await _require_map(map_id)
    asset = await asset_service.get_asset(map_id, asset_id)
    if asset is None:
        raise ValueError(
            f"Asset '{asset_id}' not found on map '{map_id}'. "
            f"Read {map_uri(map_id)}/assets for the asset index."
        )
    style = asset.style.model_dump(exclude_none=True) if asset.style else {}
    return json.dumps({"asset_id": asset_id, "style": style}, indent=2)


# ─── GeoJSON resources (application/geo+json) ────────────────────────────────

@mcp_server.resource(
    "map://{map_id}/assets/{asset_id}",
    title="Asset GeoJSON",
    description="The asset's geometry as GeoJSON, EPSG:4326 (lon, lat). For GeoTIFF "
                "assets this is the bounds footprint polygon; the rendered PNG lives "
                "at map://{map_id}/rasters/{asset_id}.",
    mime_type="application/geo+json",
)
async def res_asset(map_id: str, asset_id: str) -> str:
    await _require_map(map_id)
    asset = await asset_service.get_asset(map_id, asset_id)
    if asset is None:
        raise ValueError(
            f"Asset '{asset_id}' not found on map '{map_id}'. "
            f"Read {map_uri(map_id)}/assets for the asset index."
        )
    return json.dumps(_geojson_obj(asset), indent=2)


@mcp_server.resource(
    "map://{map_id}/drawn",
    title="User-Drawn Shapes",
    description="Everything the user drew on the map (drawn_polygon / drawn_box / ...) "
                "as one GeoJSON FeatureCollection, EPSG:4326. Feature properties carry "
                "asset_id, name, and asset_type.",
    mime_type="application/geo+json",
)
async def res_drawn(map_id: str) -> str:
    await _require_map(map_id)
    assets = await asset_service.list_assets(map_id)
    features: list[dict[str, Any]] = []
    for a in assets:
        if not a.asset_type.startswith("drawn_"):
            continue
        gj = _geojson_obj(a)
        # Normalize whatever was stored (Feature / FeatureCollection / bare
        # geometry) into Feature(s) tagged with the asset identity.
        if gj.get("type") == "FeatureCollection":
            feats = gj.get("features", [])
        elif gj.get("type") == "Feature":
            feats = [gj]
        else:  # bare geometry
            feats = [{"type": "Feature", "geometry": gj, "properties": {}}]
        for f in feats:
            props = dict(f.get("properties") or {})
            props.update({
                "asset_id": a.asset_id,
                "name": a.name,
                "asset_type": a.asset_type,
                "resource_uri": asset_uri(map_id, a.asset_id),
            })
            features.append({**f, "properties": props})
    return json.dumps(
        {"type": "FeatureCollection", "features": features}, indent=2
    )


# ─── Binary resources (image/png) ────────────────────────────────────────────

@mcp_server.resource(
    "map://{map_id}/rasters/{asset_id}",
    title="Rendered Raster",
    description="The rendered PNG overlay of a GeoTIFF asset (RGBA, web-ready). "
                "Its geographic footprint is the asset's GeoJSON at "
                "map://{map_id}/assets/{asset_id}.",
    mime_type="image/png",
)
async def res_raster(map_id: str, asset_id: str) -> bytes:
    await _require_map(map_id)
    if not _SAFE_ID.match(asset_id):
        raise ValueError(f"Invalid asset_id '{asset_id}'.")
    asset = await asset_service.get_asset(map_id, asset_id)
    if asset is None:
        raise ValueError(
            f"Asset '{asset_id}' not found on map '{map_id}'. "
            f"Read {map_uri(map_id)}/assets for the asset index."
        )
    if not asset.asset_type.startswith("geotiff"):
        raise ValueError(
            f"Asset '{asset_id}' is a '{asset.asset_type}', not a GeoTIFF raster. "
            f"Its GeoJSON lives at {asset_uri(map_id, asset_id)}."
        )
    # geotiff_service renders overlays to file_dir/{asset_id}.png.
    path = _safe_file(f"{asset_id}.png")
    if not path.exists():
        raise ValueError(
            f"Rendered PNG for raster '{asset_id}' is not on disk (it may have "
            "been cleaned up). Re-add the GeoTIFF with the add_geotiff tool."
        )
    return path.read_bytes()


@mcp_server.resource(
    "map://{map_id}/screenshots/{screenshot_id}",
    title="Map Screenshot",
    description="A captured PNG screenshot of the map (from the take_screenshot tool).",
    mime_type="image/png",
)
async def res_screenshot(map_id: str, screenshot_id: str) -> bytes:
    await _require_map(map_id)
    if not _SAFE_ID.match(screenshot_id):
        raise ValueError(f"Invalid screenshot_id '{screenshot_id}'.")
    # screenshot_service saves captures to file_dir/screenshot-{id}.png.
    path = _safe_file(f"screenshot-{screenshot_id}.png")
    if not path.exists():
        raise ValueError(
            f"Screenshot '{screenshot_id}' not found. Capture one with the "
            "take_screenshot tool; its result links the canonical resource URI."
        )
    return path.read_bytes()
