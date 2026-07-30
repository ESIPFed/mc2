"""MapSession — per-map operations."""

from __future__ import annotations

import json
import math
from typing import Any

import httpx

from .models import Asset, EventResult, ScreenshotResult, Style, Metadata, Viewport, ViewportInfo
from .exceptions import ServerError, NotFoundError


def _sanitize_floats(obj: Any) -> Any:
    """Recursively replace NaN/Inf floats with None for JSON serialization.

    Python's ``json.dumps`` raises ``ValueError: Out of range float values
    are not JSON compliant`` when it encounters ``float('nan')`` or
    ``float('inf')``.  numpy's ``np.nan`` is also a float NaN.

    This helper walks dicts/lists and converts any non-finite float to
    ``None`` (JSON ``null``), which is always safe.  Called automatically
    by ``_send_event`` so SDK callers don't need to sanitize manually.
    """
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    if isinstance(obj, dict):
        return {k: _sanitize_floats(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_floats(v) for v in obj]
    return obj


class MapSession:
    """Represents a connection to a specific map. All operations happen through this object.

    You typically get a MapSession from MapControl.create_map() or MapControl.connect_map().
    """

    def __init__(
        self,
        client: httpx.Client,
        server_url: str,
        map_id: str,
        map_url: str,
        user_session_id: str,
        session_url: str,
    ):
        self._client = client
        self._server_url = server_url
        self.map_id = map_id
        self.url = map_url
        self.user_session_id = user_session_id
        self.session_url = session_url

    # ─── Convenience Properties ──────────────────────────────────────────────

    @property
    def api_base(self) -> str:
        return f"/api/maps/{self.map_id}"

    # ─── Events (Internal) ───────────────────────────────────────────────────

    def _send_event(self, event_type: str, data: dict[str, Any]) -> EventResult:
        """Send an event to the map."""
        resp = self._client.post(
            f"{self.api_base}/events",
            json={
                "type": event_type,
                "data": _sanitize_floats(data),
                "user_session_id": self.user_session_id,
            },
        )
        if resp.status_code >= 400:
            detail = resp.text
            try:
                detail = resp.json().get("detail", resp.text)
            except Exception:
                pass
            raise ServerError(resp.status_code, detail)

        body = resp.json()

        # Check for server-side processing errors (e.g. GeoTIFF too large)
        if body.get("error"):
            raise ServerError(resp.status_code, body["error"])

        return EventResult(
            event_id=body["event_id"],
            type=body["type"],
            asset_id=body.get("asset_id"),
            created_at=body["created_at"],
        )

    # ─── Add Assets ──────────────────────────────────────────────────────────

    def add_polygon(
        self,
        geojson: str | dict,
        name: str | None = None,
        style: Style | dict | None = None,
        metadata: Metadata | dict | None = None,
        animated: bool = False,
    ) -> EventResult:
        """Add a polygon to the map from a GeoJSON string or dict."""
        data = self._build_asset_data(geojson, name, style, metadata, animated)
        return self._send_event("add_polygon", data)

    def add_polygon_url(
        self,
        url: str,
        name: str | None = None,
        style: Style | dict | None = None,
        metadata: Metadata | dict | None = None,
        animated: bool = False,
    ) -> EventResult:
        """Add a polygon from a URL pointing to GeoJSON."""
        data = {"url": url}
        if name:
            data["name"] = name
        if style:
            data["style"] = style.to_dict() if isinstance(style, Style) else style
        if metadata:
            data["metadata"] = metadata.to_dict() if isinstance(metadata, Metadata) else metadata
        if animated:
            data["animated"] = True
        return self._send_event("add_polygon_url", data)

    def add_path(
        self,
        geojson: str | dict,
        name: str | None = None,
        style: Style | dict | None = None,
        metadata: Metadata | dict | None = None,
        animated: bool = False,
    ) -> EventResult:
        """Add a path/linestring to the map."""
        data = self._build_asset_data(geojson, name, style, metadata, animated)
        return self._send_event("add_path", data)

    def add_path_url(
        self,
        url: str,
        name: str | None = None,
        style: Style | dict | None = None,
        metadata: Metadata | dict | None = None,
        animated: bool = False,
    ) -> EventResult:
        """Add a path from a URL pointing to GeoJSON."""
        data = {"url": url}
        if name:
            data["name"] = name
        if style:
            data["style"] = style.to_dict() if isinstance(style, Style) else style
        if metadata:
            data["metadata"] = metadata.to_dict() if isinstance(metadata, Metadata) else metadata
        if animated:
            data["animated"] = True
        return self._send_event("add_path_url", data)

    def add_point(
        self,
        geojson: str | dict,
        name: str | None = None,
        style: Style | dict | None = None,
        metadata: Metadata | dict | None = None,
    ) -> EventResult:
        """Add a point to the map."""
        data = self._build_asset_data(geojson, name, style, metadata, False)
        return self._send_event("add_point", data)

    def add_arc(
        self,
        from_point: list[float],
        to_point: list[float],
        from_name: str | None = None,
        to_name: str | None = None,
        name: str | None = None,
        style: Style | dict | None = None,
        curvature: float = 0.25,
    ) -> EventResult:
        """Draw a visibly bowed arc between two [lon, lat] points.

        The server tessellates a curved LineString and adds circle markers
        at both endpoints. When from_name/to_name are given the endpoints
        are labeled automatically (style.label overrides).

        Args:
            from_point: Start [lon, lat] (EPSG:4326).
            to_point: End [lon, lat] (EPSG:4326).
            from_name: Optional label for the start point.
            to_name: Optional label for the end point.
            name: Optional display name for the arc asset.
            style: Optional Style (stroke_* for the arc line, fill_color for
                the endpoint dots, line_dash for a dashed arc, label_*).
            curvature: Sideways bow as a fraction of the distance (default
                0.25). Negative bows the other way; 0 = true great circle.
        """
        data: dict[str, Any] = {"from": from_point, "to": to_point,
                                "curvature": curvature}
        if from_name:
            data["from_name"] = from_name
        if to_name:
            data["to_name"] = to_name
        if name:
            data["name"] = name
        if style:
            data["style"] = style.to_dict() if isinstance(style, Style) else style
        return self._send_event("add_arc", data)

    # ─── Batch Add Assets ─────────────────────────────────────────────────────

    def add_polygons(
        self,
        items: list[dict],
        default_style: Style | dict | None = None,
    ) -> list[EventResult]:
        """Add multiple polygons to the map in a single call.

        Each item in the list should be a dict with:
          - geojson (str | dict): Required. The GeoJSON geometry or Feature.
          - name (str): Optional. A label for this asset.
          - style (Style | dict): Optional. Per-item style override.
          - metadata (Metadata | dict): Optional.
          - animated (bool): Optional. Default False.

        Args:
            items: List of polygon descriptors.
            default_style: Style applied to items that don't specify their own.

        Returns:
            List of EventResult, one per created asset.
        """
        return self._batch_add("add_polygons", "polygon", items, default_style)

    def add_points(
        self,
        items: list[dict],
        default_style: Style | dict | None = None,
    ) -> list[EventResult]:
        """Add multiple points to the map in a single call.

        Each item: {geojson, name?, style?, metadata?}

        Args:
            items: List of point descriptors.
            default_style: Style applied to items that don't specify their own.

        Returns:
            List of EventResult, one per created asset.
        """
        return self._batch_add("add_points", "point", items, default_style)

    def add_paths(
        self,
        items: list[dict],
        default_style: Style | dict | None = None,
    ) -> list[EventResult]:
        """Add multiple paths/linestrings to the map in a single call.

        Each item: {geojson, name?, style?, metadata?}

        Args:
            items: List of path descriptors.
            default_style: Style applied to items that don't specify their own.

        Returns:
            List of EventResult, one per created asset.
        """
        return self._batch_add("add_paths", "path", items, default_style)

    def _batch_add(
        self,
        event_type: str,
        asset_type: str,
        items: list[dict],
        default_style: Style | dict | None = None,
    ) -> list[EventResult]:
        """Internal: send a batch add event and parse the multi-asset response."""
        serialized_items = []
        for item in items:
            entry: dict[str, Any] = {}
            geojson = item.get("geojson", item.get("geo_json"))
            if isinstance(geojson, dict):
                geojson = json.dumps(geojson)
            entry["geojson"] = geojson
            if item.get("name"):
                entry["name"] = item["name"]
            # Per-item style, or fall back to default
            style = item.get("style") or default_style
            if style:
                entry["style"] = style.to_dict() if isinstance(style, Style) else style
            if item.get("metadata"):
                m = item["metadata"]
                entry["metadata"] = m.to_dict() if isinstance(m, Metadata) else m
            if item.get("animated"):
                entry["animated"] = True
            serialized_items.append(entry)

        resp = self._client.post(
            f"{self.api_base}/events",
            json={
                "type": event_type,
                "data": {"items": serialized_items},
                "user_session_id": self.user_session_id,
            },
        )
        if resp.status_code >= 400:
            detail = resp.text
            try:
                detail = resp.json().get("detail", resp.text)
            except Exception:
                pass
            raise ServerError(resp.status_code, detail)

        body = resp.json()

        if body.get("error"):
            raise ServerError(resp.status_code, body["error"])

        # Batch response has asset_ids list
        asset_ids = body.get("asset_ids", [])
        results = []
        for aid in asset_ids:
            results.append(EventResult(
                event_id=body["event_id"],
                type=body["type"],
                asset_id=aid,
                created_at=body["created_at"],
            ))
        # If only one came back (shouldn't happen), still return a list
        if not results and body.get("asset_id"):
            results.append(EventResult(
                event_id=body["event_id"],
                type=body["type"],
                asset_id=body.get("asset_id"),
                created_at=body["created_at"],
            ))
        return results

    # ─── GeoTIFF Overlays ────────────────────────────────────────────────────

    def add_geotiff_rgb(
        self, url: str, name: str | None = None, bands: list[int] | None = None,
        alpha: float = 1.0, nodata: float | None = None,
    ) -> EventResult:
        """Overlay an RGB GeoTIFF on the map. Accepts URL or absolute local file path."""
        data: dict[str, Any] = {"url": url}
        if name: data["name"] = name
        if bands: data["bands"] = bands
        if alpha != 1.0: data["alpha"] = alpha
        if nodata is not None: data["nodata"] = nodata
        return self._send_event("add_geotiff_rgb", data)

    def add_geotiff_singleband(
        self, url: str, name: str | None = None, band: int = 1, colormap: str = "viridis",
        alpha: float = 1.0, vmin: float | None = None, vmax: float | None = None,
        percentile_min: float | None = None, percentile_max: float | None = None,
        nodata: float | None = None,
    ) -> EventResult:
        """Overlay a single-band GeoTIFF with colormap. Accepts URL or absolute local file path."""
        data: dict[str, Any] = {"url": url, "band": band, "colormap": colormap}
        if name: data["name"] = name
        if alpha != 1.0: data["alpha"] = alpha
        if vmin is not None: data["vmin"] = vmin
        if vmax is not None: data["vmax"] = vmax
        if percentile_min is not None: data["percentile_min"] = percentile_min
        if percentile_max is not None: data["percentile_max"] = percentile_max
        if nodata is not None: data["nodata"] = nodata
        return self._send_event("add_geotiff_singleband", data)

    # ─── Zoom ────────────────────────────────────────────────────────────────

    def zoom_to_point(self, lon: float, lat: float, zoom: int = 14) -> EventResult:
        """Zoom to a specific point with animated transition."""
        return self._send_event("zoom_to_point", {"lon": lon, "lat": lat, "zoom": zoom})

    def zoom_to_bbox(self, bbox: list[float]) -> EventResult:
        """Zoom to a bounding box [minLon, minLat, maxLon, maxLat]."""
        return self._send_event("zoom_to_bbox", {"bbox": bbox})

    def zoom_to_assets(self, asset_ids: list[str]) -> EventResult:
        """Zoom to fit all specified assets in view."""
        return self._send_event("zoom_to_assets", {"asset_ids": asset_ids})

    def zoom_out(self, levels: int = 1) -> EventResult:
        """Zoom out by the specified number of levels."""
        return self._send_event("zoom_out", {"levels": levels})

    # ─── Asset Management ────────────────────────────────────────────────────

    def list_assets(self) -> list[Asset]:
        """List all assets on this map."""
        resp = self._client.get(f"{self.api_base}/assets")
        if resp.status_code >= 400:
            raise ServerError(resp.status_code, resp.text)

        return [self._parse_asset(a) for a in resp.json()]

    def get_asset(self, asset_id: str) -> Asset:
        """Get a specific asset by ID."""
        resp = self._client.get(f"{self.api_base}/assets/{asset_id}")
        if resp.status_code == 404:
            raise NotFoundError(f"Asset {asset_id} not found")
        if resp.status_code >= 400:
            raise ServerError(resp.status_code, resp.text)
        return self._parse_asset(resp.json())

    def delete_asset(self, asset_id: str) -> EventResult:
        """Delete an asset from the map."""
        return self._send_event("delete_asset", {"asset_id": asset_id})

    def set_visibility(self, asset_id: str, visible: bool = True) -> EventResult:
        """Toggle asset visibility."""
        return self._send_event("set_visibility", {"asset_id": asset_id, "visible": visible})

    def update_style(self, asset_id: str, style: Style | dict) -> EventResult:
        """Update the style of an asset."""
        style_dict = style.to_dict() if isinstance(style, Style) else style
        return self._send_event("update_style", {"asset_id": asset_id, "style": style_dict})

    # ─── Tile Layers ─────────────────────────────────────────────────────────

    def add_tile_layer(
        self,
        url: str,
        name: str | None = None,
        opacity: float = 1.0,
        attribution: str | None = None,
    ) -> EventResult:
        """Add a raster tile layer (XYZ, WMS, etc.) to the map.

        Args:
            url: Tile URL template with {z}/{x}/{y} placeholders.
            name: Display name for the layer.
            opacity: Layer opacity (0.0-1.0). Default 1.0.
            attribution: Attribution text for the tile provider.
        """
        data: dict[str, Any] = {"url": url, "opacity": opacity}
        if name:
            data["name"] = name
        if attribution:
            data["attribution"] = attribution
        return self._send_event("add_tile_layer", data)

    def remove_tile_layer(self, asset_id: str) -> EventResult:
        """Remove a tile layer from the map."""
        return self._send_event("remove_tile_layer", {"asset_id": asset_id})

    # ─── Opacity ─────────────────────────────────────────────────────────────

    def set_opacity(self, asset_id: str, opacity: float) -> EventResult:
        """Set the opacity of any asset (vector, raster, or tile layer).

        Args:
            asset_id: The asset to change opacity for.
            opacity: Opacity value (0.0 = transparent, 1.0 = opaque).
        """
        return self._send_event("set_opacity", {
            "asset_id": asset_id,
            "opacity": max(0.0, min(1.0, opacity)),
        })

    # ─── Layer Ordering ──────────────────────────────────────────────────────

    def move_layer(self, asset_id: str, position: str = "top") -> EventResult:
        """Change the z-order of a layer on the map.

        Args:
            asset_id: The asset to reorder.
            position: One of 'top', 'bottom', 'up', 'down'.
        """
        return self._send_event("move_layer", {
            "asset_id": asset_id,
            "position": position,
        })

    # ─── Basemap ─────────────────────────────────────────────────────────────

    def set_basemap(self, basemap: str) -> EventResult:
        """Switch the basemap. The id must match a key in the server's
        ``[map.basemaps]`` config (e.g. ``osm``, ``satellite``,
        ``maptiler_streets``). Unknown ids are logged and ignored
        client-side."""
        return self._send_event("set_basemap", {"basemap": basemap})

    def set_theme(self, theme: str = "auto") -> EventResult:
        """Set the map's UI theme (light/dark mode) — applies live to all
        connected viewers without a reload.

        Args:
            theme: 'dark' or 'light' pins the appearance; 'auto' (default)
                follows each viewer's OS/browser color-scheme preference.
                The theme is map-level state and persists across reloads.

        Returns:
            EventResult confirming the event was sent.
        """
        return self._send_event("set_theme", {"theme": theme})

    # ─── Terrain (2D/3D) ─────────────────────────────────────────────────────

    def set_terrain(self, mode: str = "3d") -> EventResult:
        """Switch between 2D and 3D terrain mode.

        In 3D mode, the map tilts to a 60-degree pitch with real elevation
        from AWS Terrain Tiles and an atmospheric sky effect. In 2D mode,
        the map returns to a flat overhead view.

        Args:
            mode: '3d' to enable terrain + pitch, '2d' to return to flat view.
                  Defaults to '3d'.

        Returns:
            EventResult confirming the event was sent.
        """
        return self._send_event("set_terrain", {"mode": mode})

    # ─── Pitch / Bearing ─────────────────────────────────────────────────────

    def set_pitch(self, pitch: float, bearing: float | None = None, duration: int = 1000) -> EventResult:
        """Set the camera pitch (tilt) and optionally bearing (rotation).

        Args:
            pitch: Camera tilt in degrees (0 = overhead, 85 = near-horizon).
            bearing: Map rotation in degrees (0 = north up). None = unchanged.
            duration: Animation duration in milliseconds. Default 1000.

        Returns:
            EventResult confirming the event was sent.
        """
        data: dict = {"pitch": pitch, "duration": duration}
        if bearing is not None:
            data["bearing"] = bearing
        return self._send_event("set_pitch", data)

    # ─── Drawing ─────────────────────────────────────────────────────────────

    def enable_drawing(self, mode: str = "polygon") -> EventResult:
        """Enable drawing mode on the map in the browser.

        This sends a command to the connected browser session to activate
        drawing tools. The user can then draw on the map interactively.

        Args:
            mode: Drawing mode - 'polygon' (click vertices, double-click to finish)
                  or 'box' (click two corners to draw a rectangle).

        Returns:
            EventResult confirming the event was sent.
        """
        return self._send_event("enable_drawing", {"mode": mode})

    def disable_drawing(self) -> EventResult:
        """Disable drawing mode and cancel any in-progress drawing.

        Returns:
            EventResult confirming the event was sent.
        """
        return self._send_event("disable_drawing", {})

    def list_drawn_assets(self) -> list[Asset]:
        """List only user-drawn assets on this map.

        Returns assets with asset_type starting with 'drawn_' (e.g., 'drawn_polygon',
        'drawn_box'). These are assets created by users drawing on the map.

        Returns:
            List of drawn Asset objects.
        """
        all_assets = self.list_assets()
        return [a for a in all_assets if a.asset_type.startswith("drawn_")]

    def get_drawn_features_geojson(self) -> dict:
        """Get all user-drawn features as a GeoJSON FeatureCollection.

        Convenient for downloading/exporting drawn features. Each drawn asset's
        GeoJSON is included as a Feature with the asset_id and name in properties.

        Returns:
            A GeoJSON FeatureCollection dict ready for serialization.
        """
        drawn = self.list_drawn_assets()
        features = []
        for asset in drawn:
            geojson = asset.geojson
            if isinstance(geojson, str):
                geojson = json.loads(geojson)

            # Ensure it's a Feature with properties
            if geojson.get("type") == "Feature":
                feature = geojson
                # Enrich properties with asset info
                props = feature.get("properties", {}) or {}
                props["asset_id"] = asset.asset_id
                props["name"] = asset.name
                props["asset_type"] = asset.asset_type
                feature["properties"] = props
            else:
                # Wrap bare geometry in a Feature
                feature = {
                    "type": "Feature",
                    "properties": {
                        "asset_id": asset.asset_id,
                        "name": asset.name,
                        "asset_type": asset.asset_type,
                    },
                    "geometry": geojson,
                }
            features.append(feature)

        return {
            "type": "FeatureCollection",
            "features": features,
        }

    # ─── Screenshot ──────────────────────────────────────────────────────────

    def take_screenshot(self) -> ScreenshotResult:
        """Take a screenshot of the map as currently rendered in the browser.

        This captures the exact visual state of the map canvas, including basemap
        tiles, all assets (polygons, paths, points, GeoTIFFs), at the current
        viewport (zoom, center, pitch, bearing).

        The screenshot is saved as a PNG on the server and a URL is returned.

        Requires an active browser session (the map must be open in a browser).

        Returns:
            ScreenshotResult with screenshot_id, url, full_url, and filename.

        Raises:
            ServerError: If no browser is connected (409) or capture fails.
        """
        resp = self._client.post(
            f"{self.api_base}/sessions/{self.user_session_id}/screenshot",
        )
        if resp.status_code >= 400:
            detail = resp.text
            try:
                detail = resp.json().get("detail", resp.text)
            except Exception:
                pass
            raise ServerError(resp.status_code, detail)

        body = resp.json()
        return ScreenshotResult(
            screenshot_id=body["screenshot_id"],
            url=body["url"],
            full_url=body["full_url"],
            filename=body["filename"],
        )

    # ─── Viewport ────────────────────────────────────────────────────────────

    def get_viewport(self) -> ViewportInfo:
        """Get the current viewport state."""
        resp = self._client.get(
            f"{self.api_base}/sessions/{self.user_session_id}/viewport"
        )
        if resp.status_code >= 400:
            raise ServerError(resp.status_code, resp.text)

        body = resp.json()
        vp = None
        if body.get("viewport"):
            vp = Viewport(
                center=body["viewport"].get("center"),
                zoom=body["viewport"].get("zoom"),
                bbox=body["viewport"].get("bbox"),
            )
        return ViewportInfo(
            viewport=vp,
            basemap=body.get("basemap", "osm"),
            contained_assets=body.get("contained_assets", []),
            partial_assets=body.get("partial_assets", []),
        )

    # ─── Helpers ─────────────────────────────────────────────────────────────

    def _build_asset_data(
        self,
        geojson: str | dict,
        name: str | None,
        style: Style | dict | None,
        metadata: Metadata | dict | None,
        animated: bool,
    ) -> dict:
        """Build the data dict for an add_* event."""
        if isinstance(geojson, dict):
            geojson = json.dumps(geojson)

        data: dict[str, Any] = {"geojson": geojson}
        if name:
            data["name"] = name
        if style:
            data["style"] = style.to_dict() if isinstance(style, Style) else style
        if metadata:
            data["metadata"] = metadata.to_dict() if isinstance(metadata, Metadata) else metadata
        if animated:
            data["animated"] = True
        return data

    @staticmethod
    def _parse_asset(data: dict) -> Asset:
        """Parse a server asset response into an Asset object."""
        style = None
        if data.get("style"):
            s = data["style"]
            style = Style(
                fill_color=s.get("fill_color"),
                stroke_color=s.get("stroke_color"),
                stroke_width=s.get("stroke_width"),
                line_dash=s.get("line_dash"),
                label=s.get("label"),
                label_color=s.get("label_color"),
                label_size=s.get("label_size"),
                label_placement=s.get("label_placement"),
                color_by=s.get("color_by"),
            )
        metadata = None
        if data.get("metadata"):
            m = data["metadata"]
            metadata = Metadata(
                title=m.get("title"),
                description=m.get("description"),
                extra=m.get("extra", {}),
            )
        return Asset(
            asset_id=data["asset_id"],
            map_id=data["map_id"],
            name=data.get("name"),
            asset_type=data.get("asset_type", ""),
            geojson=data.get("geojson", ""),
            style=style,
            metadata=metadata,
            visible=data.get("visible", True),
            animated=data.get("animated", False),
            source_url=data.get("source_url"),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
        )

    def __repr__(self) -> str:
        return f"MapSession(map_id={self.map_id!r}, url={self.session_url!r})"
