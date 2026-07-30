"""Event creation, persistence, and dispatch logic."""

from __future__ import annotations

import json
import math
import uuid
from datetime import datetime, timezone

from ..database import get_db
from ..config import load_config
from ..models import (
    AssetMetadata,
    AssetStyle,
    MapEvent,
    MapEventResponse,
    EventListItem,
)
from ..websocket import manager
from . import asset_service
from . import geotiff_service
from .geotiff_service import GeoTIFFError, _bounds_to_geojson_polygon


def _tessellate_great_circle(
    start: list[float], end: list[float], num_points: int = 64
) -> list[list[float]]:
    """Interpolate a great-circle arc between two [lon, lat] points (slerp).

    Returns num_points+1 coordinates including both endpoints. Note: no
    antimeridian splitting — arcs crossing ±180° render with a wrap line.
    """
    lon1, lat1 = math.radians(start[0]), math.radians(start[1])
    lon2, lat2 = math.radians(end[0]), math.radians(end[1])
    v1 = (
        math.cos(lat1) * math.cos(lon1),
        math.cos(lat1) * math.sin(lon1),
        math.sin(lat1),
    )
    v2 = (
        math.cos(lat2) * math.cos(lon2),
        math.cos(lat2) * math.sin(lon2),
        math.sin(lat2),
    )
    dot = max(-1.0, min(1.0, sum(a * b for a, b in zip(v1, v2))))
    omega = math.acos(dot)
    if omega < 1e-9:
        return [list(start), list(end)]
    sin_omega = math.sin(omega)
    coords: list[list[float]] = []
    for i in range(num_points + 1):
        t = i / num_points
        s1 = math.sin((1 - t) * omega) / sin_omega
        s2 = math.sin(t * omega) / sin_omega
        x = s1 * v1[0] + s2 * v2[0]
        y = s1 * v1[1] + s2 * v2[1]
        z = s1 * v1[2] + s2 * v2[2]
        coords.append([math.degrees(math.atan2(y, x)), math.degrees(math.asin(z))])
    return coords


def _build_arc_geojson(data: dict) -> str:
    """Build the arc FeatureCollection: geodesic LineString + endpoint dots.

    data: {from: [lon,lat], to: [lon,lat], from_name?, to_name?, name?,
           curvature?}. The stored LineString is ALWAYS the great-circle
    geodesic: it is only visible as the FALLBACK rendering (globe mode, or
    deck.gl unavailable), where a sideways Bézier bow drawn flat on the
    surface reads as a mistake. `curvature` still shapes the 3D deck
    ribbon's height client-side — it just no longer bows the fallback.
    The endpoint Point features carry `name` properties so the frontend's
    label layer (style.label=True, label_placement='point') renders the
    names over the dots — sketch 003's "arc between two named locations".
    """
    start = data["from"]
    end = data["to"]
    line_coords = _tessellate_great_circle(start, end)
    from_name = data.get("from_name")
    to_name = data.get("to_name")
    arc_name = data.get("name") or (
        f"{from_name} \u2194 {to_name}" if from_name and to_name else "Arc"
    )
    features = [
        {
            "type": "Feature",
            "properties": {"kind": "arc-line"},
            "geometry": {
                "type": "LineString",
                "coordinates": line_coords,
            },
        },
        {
            "type": "Feature",
            "properties": {"kind": "arc-endpoint", "name": from_name or ""},
            "geometry": {"type": "Point", "coordinates": list(start)},
        },
        {
            "type": "Feature",
            "properties": {"kind": "arc-endpoint", "name": to_name or ""},
            "geometry": {"type": "Point", "coordinates": list(end)},
        },
    ]
    return json.dumps(
        {"type": "FeatureCollection", "features": features, "properties": {"name": arc_name}}
    )


async def process_event(map_id: str, event: MapEvent) -> MapEventResponse:
    """Process an incoming map event: persist it, handle side effects, broadcast."""
    db = await get_db()
    event_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    # Persist the event
    await db.execute(
        "INSERT INTO events (id, map_id, event_type, event_data, created_at) VALUES (?, ?, ?, ?, ?)",
        (event_id, map_id, event.type, json.dumps(event.data), now),
    )
    await db.commit()

    asset_id = None

    # Handle asset-creating events (create the asset in the DB)
    if event.type in ("add_polygon", "add_path", "add_point"):
        style = AssetStyle(**event.data["style"]) if "style" in event.data else None
        metadata = (
            AssetMetadata(**event.data["metadata"])
            if "metadata" in event.data
            else None
        )
        asset_type = event.type.replace("add_", "")
        asset = await asset_service.create_asset(
            map_id=map_id,
            asset_type=asset_type,
            geojson=event.data.get("geojson", ""),
            name=event.data.get("name"),
            style=style,
            metadata=metadata,
            animated=event.data.get("animated", False),
        )
        asset_id = asset.asset_id
        # Enrich the broadcast data with the assigned asset_id
        event.data["asset_id"] = asset_id

    elif event.type == "add_arc":
        # Arc glyph: server tessellates a great-circle LineString between two
        # points and stores it as an ordinary vector asset (asset_type="arc").
        # Endpoint dots + optional names ride along as Point features in the
        # same FeatureCollection — a macro over existing primitives, no new
        # rendering machinery (see docs/vision/asset-style-ir.md).
        if "from" not in event.data or "to" not in event.data:
            return MapEventResponse(
                event_id=event_id,
                type=event.type,
                asset_id=None,
                created_at=now,
                error="add_arc requires 'from' and 'to' as [lon, lat] pairs",
            )
        style_data = dict(event.data.get("style") or {})
        # Auto-label the endpoints when names were provided and the caller
        # didn't explicitly configure labels.
        if (event.data.get("from_name") or event.data.get("to_name")) and (
            "label" not in style_data
        ):
            style_data["label"] = True
            style_data.setdefault("label_placement", "point")
        # Default the FALLBACK line to DOTTED (tiny dash, wide gap): the flat
        # geodesic only shows when the 3D deck ribbon can't render (globe
        # projection / deck unavailable), and a dotted route line reads as
        # deliberate there. The deck ribbon ignores line_dash, so mercator's
        # solid 3D ribbon is unaffected. Callers may override explicitly.
        style_data.setdefault("line_dash", [0.5, 2])
        style = AssetStyle(**style_data) if style_data else None
        metadata = (
            AssetMetadata(**event.data["metadata"])
            if "metadata" in event.data
            else None
        )
        arc_geojson = _build_arc_geojson(event.data)
        arc_name = event.data.get("name") or (
            f"{event.data.get('from_name', '')} \u2194 {event.data.get('to_name', '')}".strip(" \u2194 ")
            or "Arc"
        )
        asset = await asset_service.create_asset(
            map_id=map_id,
            asset_type="arc",
            geojson=arc_geojson,
            name=arc_name,
            style=style,
            metadata=metadata,
        )
        asset_id = asset.asset_id
        # Enrich broadcast so the frontend can render without re-tessellating
        event.data["asset_id"] = asset_id
        event.data["geojson"] = arc_geojson
        event.data["name"] = arc_name
        if style is not None:
            event.data["style"] = style.model_dump(exclude_none=True)

    elif event.type in ("add_polygon_url", "add_path_url"):
        style = AssetStyle(**event.data["style"]) if "style" in event.data else None
        metadata = (
            AssetMetadata(**event.data["metadata"])
            if "metadata" in event.data
            else None
        )
        asset_type = event.type.replace("add_", "").replace("_url", "")
        asset = await asset_service.create_asset_from_url(
            map_id=map_id,
            asset_type=asset_type,
            url=event.data["url"],
            name=event.data.get("name"),
            style=style,
            metadata=metadata,
            animated=event.data.get("animated", False),
        )
        asset_id = asset.asset_id
        # Send the resolved geojson to the frontend
        event.data["asset_id"] = asset_id
        event.data["geojson"] = asset.geojson

    elif event.type == "delete_asset":
        target_id = event.data.get("asset_id")
        if target_id:
            await asset_service.delete_asset(map_id, target_id)

    elif event.type == "add_tile_layer":
        # Create a lightweight asset to track the tile layer
        asset = await asset_service.create_asset(
            map_id=map_id,
            asset_type="tile_layer",
            geojson="{}",  # tile layers don't have geometry
            name=event.data.get("name", "Tile Layer"),
            source_url=event.data.get("url", ""),
        )
        asset_id = asset.asset_id
        event.data["asset_id"] = asset_id

    elif event.type == "remove_tile_layer":
        target_id = event.data.get("asset_id")
        if target_id:
            await asset_service.delete_asset(map_id, target_id)

    elif event.type == "set_visibility":
        target_id = event.data.get("asset_id")
        visible = event.data.get("visible", True)
        if target_id:
            from ..models import AssetUpdate
            await asset_service.update_asset(
                map_id, target_id, AssetUpdate(visible=visible)
            )

    elif event.type == "update_style":
        target_id = event.data.get("asset_id")
        style_data = event.data.get("style", {})
        if target_id:
            from ..models import AssetUpdate
            await asset_service.update_asset(
                map_id, target_id, AssetUpdate(style=AssetStyle(**style_data))
            )

    elif event.type == "set_theme":
        # Map-level UI theme (light | dark | auto). Persist so new viewers /
        # session restores get it, then broadcast so connected viewers flip
        # live. Unknown values are clamped to "auto" by the service.
        from . import session_service as _session_service
        normalized = await _session_service.set_map_theme(
            map_id, event.data.get("theme", "auto")
        )
        event.data["theme"] = normalized

    elif event.type == "set_basemap":
        # Pin the basemap at MAP level so it survives page reloads and takes
        # priority over the theme-appropriate default in serve_map. (The
        # per-session copy is still updated by viewport_update as before.)
        from . import session_service as _session_service
        basemap = event.data.get("basemap")
        if basemap:
            await _session_service.set_map_basemap(map_id, basemap)

    # ─── Camera events: persist intent at MAP level ────────────────────
    # zoom_* used to be broadcast-only (live viewers moved, nothing stored),
    # so a NEW viewer — critically the headless screenshot page, which always
    # starts a fresh session — booted at the world default and rendered the
    # globe instead of what the agent had just framed. Persisting the
    # requested view in maps.config (same materialization pattern as
    # theme/basemap) makes camera intent survive with or without any browser
    # ever being open. zoom_out is relative and stays ephemeral.
    elif event.type == "zoom_to_point":
        from ..models import Viewport as _Viewport
        from . import session_service as _session_service
        lon, lat = event.data.get("lon"), event.data.get("lat")
        if lon is not None and lat is not None:
            await _session_service.set_map_viewport(
                map_id,
                _Viewport(center=[lon, lat], zoom=event.data.get("zoom", 14)),
            )

    elif event.type == "zoom_to_bbox":
        from ..models import Viewport as _Viewport
        from . import session_service as _session_service
        bbox = event.data.get("bbox")
        if bbox and len(bbox) == 4:
            await _session_service.set_map_viewport(map_id, _Viewport(bbox=bbox))

    elif event.type == "zoom_to_assets":
        from ..models import Viewport as _Viewport
        from . import session_service as _session_service
        target_ids = set(event.data.get("asset_ids") or [])
        if target_ids:
            rows_assets = await asset_service.list_assets(map_id)
            geojsons = [a.geojson for a in rows_assets if a.asset_id in target_ids]
            bbox = _session_service._compute_bbox_from_geojsons(geojsons)
            if bbox:
                await _session_service.set_map_viewport(map_id, _Viewport(bbox=bbox))

    # ─── Batch asset events (add_polygons, add_points, add_paths) ─────
    elif event.type in ("add_polygons", "add_points", "add_paths"):
        # Map batch event type to singular asset type
        type_map = {
            "add_polygons": ("polygon", "add_polygon"),
            "add_points": ("point", "add_point"),
            "add_paths": ("path", "add_path"),
        }
        singular_asset_type, singular_event_type = type_map[event.type]
        items = event.data.get("items", [])
        created_asset_ids = []

        for item in items:
            item_style = AssetStyle(**item["style"]) if "style" in item else None
            item_metadata = (
                AssetMetadata(**item["metadata"])
                if "metadata" in item
                else None
            )
            asset = await asset_service.create_asset(
                map_id=map_id,
                asset_type=singular_asset_type,
                geojson=item.get("geojson", ""),
                name=item.get("name"),
                style=item_style,
                metadata=item_metadata,
                animated=item.get("animated", False),
            )
            created_asset_ids.append(asset.asset_id)

            # Broadcast each item individually so existing frontend handlers work
            item_broadcast = {
                "type": singular_event_type,
                "event_id": event_id,
                "data": {
                    "asset_id": asset.asset_id,
                    "geojson": item.get("geojson", ""),
                    **({"style": item["style"]} if "style" in item else {}),
                    **({"name": item["name"]} if "name" in item else {}),
                },
            }
            await manager.broadcast_to_map(map_id, item_broadcast)

        # Update map timestamp
        await db.execute(
            "UPDATE maps SET updated_at = ? WHERE id = ?", (now, map_id)
        )
        await db.commit()

        return MapEventResponse(
            event_id=event_id,
            type=event.type,
            asset_id=created_asset_ids[0] if len(created_asset_ids) == 1 else None,
            asset_ids=created_asset_ids,
            created_at=now,
        )

    # ─── GeoTIFF events ──────────────────────────────────────────────────
    elif event.type in ("add_geotiff_rgb", "add_geotiff_singleband"):
        config = load_config()
        source = event.data.get("url", "")
        name = event.data.get("name")
        alpha = event.data.get("alpha", 1.0)
        nodata = event.data.get("nodata")

        # Generate asset ID early so we can use it for the PNG filename
        asset_id = str(uuid.uuid4())

        if event.type == "add_geotiff_rgb":
            bands = event.data.get("bands", [1, 2, 3])
            result = await geotiff_service.process_geotiff_rgb(
                source=source,
                asset_id=asset_id,
                bands=bands,
                alpha=alpha,
                nodata=nodata,
                config=config,
            )
        else:  # add_geotiff_singleband
            result = await geotiff_service.process_geotiff_singleband(
                source=source,
                asset_id=asset_id,
                band=event.data.get("band", 1),
                colormap=event.data.get("colormap", "viridis"),
                alpha=alpha,
                vmin=event.data.get("vmin"),
                vmax=event.data.get("vmax"),
                percentile_min=event.data.get("percentile_min"),
                percentile_max=event.data.get("percentile_max"),
                nodata=nodata,
                config=config,
            )

        if isinstance(result, GeoTIFFError):
            # Return error response — don't broadcast
            return MapEventResponse(
                event_id=event_id,
                type=event.type,
                asset_id=None,
                created_at=now,
                error=result.detail,
            )

        # Create asset in DB with bounds as GeoJSON polygon
        # Pass the same asset_id used for the PNG filename so they match on restore
        bounds_geojson = _bounds_to_geojson_polygon(result.bounds)
        mode = "rgb" if event.type == "add_geotiff_rgb" else "singleband"
        asset = await asset_service.create_asset(
            map_id=map_id,
            asset_type=f"geotiff_{mode}",
            geojson=json.dumps(bounds_geojson),
            name=name or f"GeoTIFF ({mode})",
            source_url=source,
            asset_id=asset_id,
        )

        # Enrich event data for broadcast
        event.data["asset_id"] = asset_id
        event.data["image_url"] = result.image_url
        event.data["bounds"] = result.bounds
        event.data["width"] = result.width
        event.data["height"] = result.height
        event.data["crs"] = result.crs
        event.data["band_count"] = result.band_count

    # Update map's updated_at
    await db.execute(
        "UPDATE maps SET updated_at = ? WHERE id = ?", (now, map_id)
    )
    await db.commit()

    # Build the message
    broadcast_msg = {
        "type": event.type,
        "event_id": event_id,
        "data": event.data,
    }
    if asset_id:
        broadcast_msg["asset_id"] = asset_id

    # Personal events → only the requesting session (drawing controls)
    # Everything else (zoom, basemap, assets, etc.) → broadcast to all
    # sessions on the map. Since each page gets its own map, broadcasting
    # is safe and avoids session-ID mismatch issues.
    PERSONAL_EVENTS = {
        "enable_drawing", "disable_drawing",
        "set_demo_label",
    }

    if event.type in PERSONAL_EVENTS and event.user_session_id:
        await manager.send_to_session(map_id, event.user_session_id, broadcast_msg)
    else:
        await manager.broadcast_to_map(map_id, broadcast_msg)

    return MapEventResponse(
        event_id=event_id,
        type=event.type,
        asset_id=asset_id,
        created_at=now,
    )


async def list_events(
    map_id: str, limit: int = 100, offset: int = 0
) -> list[EventListItem]:
    """List events for a map with pagination."""
    db = await get_db()
    cursor = await db.execute(
        "SELECT * FROM events WHERE map_id = ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
        (map_id, limit, offset),
    )
    rows = await cursor.fetchall()

    return [
        EventListItem(
            event_id=row["id"],
            type=row["event_type"],
            event_data=json.loads(row["event_data"]),
            created_at=row["created_at"],
        )
        for row in rows
    ]
