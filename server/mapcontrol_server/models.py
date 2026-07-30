"""Pydantic models for requests, responses, and events."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from pydantic import BaseModel, Field


# ─── Style ───────────────────────────────────────────────────────────────────

class AssetStyle(BaseModel):
    fill_color: str | None = None
    stroke_color: str | None = None
    stroke_width: float | None = None
    # Optional line dash pattern for line layers / polygon outlines, expressed
    # as a MapLibre `line-dasharray` (lengths in line-widths), e.g. [2, 2] for
    # dashed or [0.5, 1.5] for dotted. None / empty = solid.
    line_dash: list[float] | None = None
    # ─── Labels (rendered as a MapLibre symbol layer) ───
    # label: True → render the asset's name as map text; a string → custom
    # label text (overrides the name). False/None = no label.
    label: bool | str | None = None
    label_color: str | None = None  # hex; defaults to stroke_color
    label_size: float | None = None  # px, default 14
    # Where the label sits:
    #   "point"     → at the point (offset above the marker) — default for points
    #   "center"    → at the polygon/line anchor — default for polygons/paths
    #   "perimeter" → along the polygon outline / line (symbol-placement: line)
    label_placement: str | None = None
    # ─── Data-driven color (gradient across features) ───
    # {"property": "<feature property>", "ramp": "viridis|magma|blues|reds",
    #  "domain": [min, max]} — colors each feature in a FeatureCollection by
    # interpolating the ramp over the numeric property value.
    color_by: dict[str, Any] | None = None
    # ─── Glow (pulsing opacity animation) ───
    # True → defaults; or {"period": 2.0 (s per cycle),
    #  "min_opacity": 0.15, "max_opacity": 0.85, "stroke": True}.
    # The asset slowly fades between translucent and opaque — a client-side
    # rAF loop; False/None = static. Works on fills, lines, and circles.
    glow: bool | dict[str, Any] | None = None



# ─── Asset Metadata ──────────────────────────────────────────────────────────

class AssetMetadata(BaseModel):
    title: str | None = None
    description: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


# ─── Map ─────────────────────────────────────────────────────────────────────

class MapCreateResponse(BaseModel):
    map_id: str
    url: str
    created_at: str
    theme: str = "auto"  # "light" | "dark" | "auto" (follows viewer's OS)


class MapInfo(BaseModel):
    map_id: str
    created_at: str
    updated_at: str
    asset_count: int = 0


# ─── User Session ────────────────────────────────────────────────────────────

class SessionCreateResponse(BaseModel):
    user_session_id: str
    map_id: str
    url: str
    created_at: str


# ─── Events ──────────────────────────────────────────────────────────────────

class MapEvent(BaseModel):
    """An event/command sent to the map."""
    type: str
    data: dict[str, Any] = Field(default_factory=dict)
    user_session_id: str | None = None  # Routes personal events (zoom, basemap) to this session only


class MapEventResponse(BaseModel):
    """Response after an event is processed."""
    event_id: str
    type: str
    asset_id: str | None = None
    asset_ids: list[str] | None = None  # For batch events (add_polygons, add_points, add_paths)
    created_at: str
    error: str | None = None


class EventListItem(BaseModel):
    event_id: str
    type: str
    event_data: dict[str, Any]
    created_at: str


# ─── Assets ──────────────────────────────────────────────────────────────────

class AssetResponse(BaseModel):
    asset_id: str
    map_id: str
    name: str | None = None
    asset_type: str
    geojson: dict[str, Any] | str
    style: AssetStyle | None = None
    metadata: AssetMetadata | None = None
    visible: bool = True
    animated: bool = False
    source_url: str | None = None
    created_at: str
    updated_at: str


class AssetUpdate(BaseModel):
    """Partial update for an asset."""
    name: str | None = None
    style: AssetStyle | None = None
    metadata: AssetMetadata | None = None
    visible: bool | None = None
    animated: bool | None = None


# ─── Viewport ────────────────────────────────────────────────────────────────

class Viewport(BaseModel):
    center: list[float] | None = None  # [lon, lat]
    zoom: float | None = None
    bbox: list[float] | None = None  # [minLon, minLat, maxLon, maxLat]
    pitch: float | None = None  # 0-85 degrees
    bearing: float | None = None  # 0-360 degrees


class ViewportResponse(BaseModel):
    viewport: Viewport | None = None
    basemap: str = "osm"
    contained_assets: list[str] = Field(default_factory=list)
    partial_assets: list[str] = Field(default_factory=list)


# ─── Session Restore (sent via WebSocket) ────────────────────────────────────

class SessionSnapshot(BaseModel):
    """Full state snapshot for session restore."""
    assets: list[AssetResponse] = Field(default_factory=list)
    viewport: Viewport | None = None
    basemap: str = "osm"
    terrain: str = "2d"  # "2d" or "3d"
    theme: str = "auto"  # "light" | "dark" | "auto" (follows viewer's OS)
