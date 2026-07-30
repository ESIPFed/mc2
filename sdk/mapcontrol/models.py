"""Data models for the Map Control SDK."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Style:
    """Style configuration for map assets.

    Labels (rendered as MapLibre symbol text):
        label: True → render the asset's name; a string → custom label text.
        label_color: hex text color (defaults to stroke_color).
        label_size: text size in px (default 14).
        label_placement: 'point' (above a marker), 'center' (inside a
            polygon), or 'perimeter' (text along the polygon outline / line).

    Data-driven color (gradient across features):
        color_by: {"property": <feature prop>, "ramp": "viridis|magma|plasma|
            blues|reds|greens", "domain": [min, max]} — colors each feature
            in a FeatureCollection by a numeric property.

    Glow (pulsing opacity animation):
        glow: True → defaults, or {"period": 2.0 (seconds per cycle),
            "min_opacity": 0.15, "max_opacity": 0.85, "stroke": True} —
            the asset slowly fades between translucent and opaque.
    """
    fill_color: str | None = None
    stroke_color: str | None = None
    stroke_width: float | None = None
    line_dash: list[float] | None = None
    label: bool | str | None = None
    label_color: str | None = None
    label_size: float | None = None
    label_placement: str | None = None
    color_by: dict[str, Any] | None = None
    glow: bool | dict[str, Any] | None = None

    def to_dict(self) -> dict:
        return {k: v for k, v in {
            "fill_color": self.fill_color,
            "stroke_color": self.stroke_color,
            "stroke_width": self.stroke_width,
            "line_dash": self.line_dash,
            "label": self.label,
            "label_color": self.label_color,
            "label_size": self.label_size,
            "label_placement": self.label_placement,
            "color_by": self.color_by,
            "glow": self.glow,
        }.items() if v is not None}


@dataclass
class Metadata:
    """Metadata for map assets."""
    title: str | None = None
    description: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = {}
        if self.title:
            d["title"] = self.title
        if self.description:
            d["description"] = self.description
        if self.extra:
            d["extra"] = self.extra
        return d


@dataclass
class Asset:
    """Represents a geospatial asset on the map."""
    asset_id: str
    map_id: str
    name: str | None = None
    asset_type: str = ""
    geojson: str | dict = ""
    style: Style | None = None
    metadata: Metadata | None = None
    visible: bool = True
    animated: bool = False
    source_url: str | None = None
    created_at: str = ""
    updated_at: str = ""


@dataclass
class Viewport:
    """Represents a map viewport."""
    center: list[float] | None = None
    zoom: float | None = None
    bbox: list[float] | None = None
    pitch: float | None = None
    bearing: float | None = None


@dataclass
class ViewportInfo:
    """Viewport information with contained assets."""
    viewport: Viewport | None = None
    basemap: str = "osm"
    terrain: str = "2d"
    contained_assets: list[str] = field(default_factory=list)
    partial_assets: list[str] = field(default_factory=list)


@dataclass
class EventResult:
    """Result of sending an event."""
    event_id: str
    type: str
    asset_id: str | None = None
    created_at: str = ""


@dataclass
class ScreenshotResult:
    """Result of taking a screenshot."""
    screenshot_id: str
    url: str
    full_url: str
    filename: str
