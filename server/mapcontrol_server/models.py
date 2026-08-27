"""Pydantic models for requests, responses, and events."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from pydantic import BaseModel, Field, model_validator


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
    # NOTE: glow is now sugar over the generic `animate` list (an opacity
    # effect); both are kept so existing callers keep working.
    glow: bool | dict[str, Any] | None = None
    # ─── Static opacity (0..1) ───
    # Flat opacity applied to fills, lines, and circles. None = renderer
    # defaults. Composes with hover highlight; ignored while an opacity
    # animation is running (the animation wins).
    opacity: float | None = None
    # ─── Animate (generic property animation) ───
    # A list of effects driven by one shared client-side rAF loop:
    #   [{"property": "opacity"|"circle_radius"|"stroke_width",
    #     "from": 0.3, "to": 1.0, "period": 1.2, "easing": "sine"}]
    # Each effect oscillates the paint property between `from` and `to`
    # over `period` seconds. Empty list / None = no animation. `glow: true`
    # compiles to a single opacity effect for back-compat.
    #
    # Special effect: "ripple" — a sonar-ping halo ring that expands
    # outward from point markers while fading to transparent (sawtooth,
    # not sine): radius ramps `from` → `to` px per cycle as opacity ramps
    # to 0, then restarts. Optional "color" (hex) tints the halo
    # (defaults to the marker's fill color):
    #   [{"property": "ripple", "from": 8, "to": 26, "period": 1.6,
    #     "color": "#38bdf8"}]
    # Rendered as an auxiliary circle layer beneath the marker; removed
    # automatically when the animation stops. Points only.
    animate: list[dict[str, Any]] | None = None
    # ─── Status (attention-lifecycle sugar) ───
    # "active" → attention pulse (opacity + marker-size + ripple halo)
    # "done"   → animation stops, full opacity, success stroke
    # "muted"  → animation stops, grayed out (no longer under consideration)
    # Expands server-side into concrete animate/opacity/color fields (only
    # filling fields the caller left unset), so clients and session restore
    # only ever see concrete style values.
    status: str | None = None

    @model_validator(mode="after")
    def _expand_status(self) -> "AssetStyle":
        """Expand the ``status`` preset into concrete style fields.

        Explicitly-set fields always win; the preset only fills gaps. The
        status value itself is preserved so callers can read it back.
        """
        if self.status == "active":
            if self.animate is None:
                self.animate = [
                    {"property": "opacity", "from": 0.35, "to": 1.0, "period": 1.2},
                    {"property": "circle_radius", "from": 6, "to": 10, "period": 1.2},
                    {"property": "ripple", "from": 8, "to": 26, "period": 1.6},
                ]
        elif self.status == "done":
            if self.animate is None:
                self.animate = []  # stop any running animation
            if self.opacity is None:
                self.opacity = 1.0
            if self.stroke_color is None:
                self.stroke_color = "#22c55e"
            if self.stroke_width is None:
                self.stroke_width = 3
        elif self.status == "muted":
            if self.animate is None:
                self.animate = []  # stop any running animation
            if self.opacity is None:
                self.opacity = 0.35
            if self.fill_color is None:
                self.fill_color = "#9ca3af"
            if self.stroke_color is None:
                self.stroke_color = "#6b7280"
        return self



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
    z_index: int = 0
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
