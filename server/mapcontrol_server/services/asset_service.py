"""Asset CRUD operations and URL fetching."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import httpx

from ..config import load_config
from ..database import get_db
from ..models import AssetResponse, AssetStyle, AssetMetadata, AssetUpdate


def is_raster_type(asset_type: str) -> bool:
    return asset_type.startswith("geotiff") or asset_type == "tile_layer"


def with_default_style(style: AssetStyle | None, asset_type: str) -> AssetStyle | None:
    """Materialize the renderer's default colors into a vector asset's style.

    The stored style is what the map draws: a layer manager reading
    GET /assets must never see ``fill_color: null`` while the map paints the
    configured default. Only gaps are filled. A masked polygon without an
    explicit fill keeps ``fill_color`` unset, because the mask's transparent
    interior is part of its look. Rasters carry no colors.
    """
    if is_raster_type(asset_type):
        return style
    defaults = load_config().map.style
    base = style.model_dump(exclude_none=True) if style else {}
    if "fill_color" not in base and not base.get("mask"):
        base["fill_color"] = defaults.fill_color
    base.setdefault("stroke_color", defaults.stroke_color)
    base.setdefault("stroke_width", float(defaults.stroke_width))
    return AssetStyle.model_validate(base)


def merge_style(base: AssetStyle | None, patch: AssetStyle) -> AssetStyle:
    """Apply a partial style update to the stored style.

    Keys the caller sent replace stored ones; keys it did not send are kept;
    an explicit ``null`` clears a key (back to the renderer default). The
    patch's own ``status`` preset is expanded against the patch alone, then
    overlaid, which is exactly what live viewers have always shown (a
    ``status: "done"`` station keeps its fill but gains the success stroke).
    """
    merged = base.model_dump(exclude_none=True) if base else {}
    merged.update(patch.model_dump(exclude_none=True))
    for key in patch.model_fields_set:
        if getattr(patch, key) is None:
            merged.pop(key, None)
    return AssetStyle.model_validate(merged)


def _compute_bbox(geojson: str) -> list[float] | None:
    """[minLon, minLat, maxLon, maxLat] of a geojson string, None if empty."""
    from .session_service import _compute_bbox_from_geojsons

    return _compute_bbox_from_geojsons([geojson])


async def create_asset(
    map_id: str,
    asset_type: str,
    geojson: str,
    name: str | None = None,
    style: AssetStyle | None = None,
    metadata: AssetMetadata | None = None,
    animated: bool = False,
    source_url: str | None = None,
    asset_id: str | None = None,
    visible: bool = True,
    animation: dict | None = None,
) -> AssetResponse:
    """Create a new asset on a map."""
    db = await get_db()
    if asset_id is None:
        asset_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    style = with_default_style(style, asset_type)
    style_json = style.model_dump_json() if style else None
    metadata_json = metadata.model_dump_json() if metadata else None
    bbox = _compute_bbox(geojson)

    # Stacking policy, encoded HERE so the DB z_index is canonical and every
    # consumer (live map, layer managers, session restore) sees the same
    # order. Vectors stack on top (max + 1). Rasters (geotiffs/tiles) slot in
    # just BELOW the lowest vector — a full-extent raster added on top would
    # bury every polygon — by shifting everything at or above that slot up
    # one. Explicit user reorders are respected afterwards: this only picks
    # the INITIAL slot.
    is_raster = is_raster_type(asset_type)
    min_vector_z = None
    if is_raster:
        cursor = await db.execute(
            "SELECT MIN(z_index) FROM assets WHERE map_id = ? "
            "AND asset_type NOT LIKE 'geotiff%' AND asset_type != 'tile_layer'",
            (map_id,),
        )
        row = await cursor.fetchone()
        min_vector_z = row[0] if row else None
    if min_vector_z is not None:
        await db.execute(
            "UPDATE assets SET z_index = z_index + 1 WHERE map_id = ? AND z_index >= ?",
            (map_id, min_vector_z),
        )
        z_index = min_vector_z
    else:
        cursor = await db.execute(
            "SELECT COALESCE(MAX(z_index), -1) + 1 FROM assets WHERE map_id = ?",
            (map_id,),
        )
        row = await cursor.fetchone()
        z_index = row[0] if row else 0

    await db.execute(
        """INSERT INTO assets (id, map_id, name, asset_type, geojson, style, metadata, visible, animated, z_index, source_url, bbox, animation, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            asset_id,
            map_id,
            name,
            asset_type,
            geojson,
            style_json,
            metadata_json,
            int(visible),
            int(animated),
            z_index,
            source_url,
            json.dumps(bbox) if bbox else None,
            json.dumps(animation) if animation else None,
            now,
            now,
        ),
    )
    await db.commit()

    return AssetResponse(
        asset_id=asset_id,
        map_id=map_id,
        name=name,
        asset_type=asset_type,
        geojson=geojson,
        bbox=bbox,
        style=style,
        metadata=metadata,
        visible=visible,
        animated=animated,
        z_index=z_index,
        source_url=source_url,
        animation=animation,
        created_at=now,
        updated_at=now,
    )


async def create_asset_from_url(
    map_id: str,
    asset_type: str,
    url: str,
    name: str | None = None,
    style: AssetStyle | None = None,
    metadata: AssetMetadata | None = None,
    animated: bool = False,
) -> AssetResponse:
    """Fetch GeoJSON from a URL and create an asset."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, timeout=30.0)
        resp.raise_for_status()
        geojson = resp.text

    return await create_asset(
        map_id=map_id,
        asset_type=asset_type,
        geojson=geojson,
        name=name,
        style=style,
        metadata=metadata,
        animated=animated,
        source_url=url,
    )


def _parse_bbox(raw: str | None) -> list[float] | None:
    if not raw:
        return None
    try:
        bbox = json.loads(raw)
    except (ValueError, TypeError):
        return None
    return bbox if isinstance(bbox, list) and len(bbox) == 4 else None


def _parse_animation(row) -> dict | None:
    raw = row["animation"] if "animation" in row.keys() else None
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except (ValueError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def _row_to_asset(row, geojson: str | None) -> AssetResponse:
    style = AssetStyle.model_validate_json(row["style"]) if row["style"] else None
    metadata = (
        AssetMetadata.model_validate_json(row["metadata"])
        if row["metadata"]
        else None
    )
    return AssetResponse(
        asset_id=row["id"],
        map_id=row["map_id"],
        name=row["name"],
        asset_type=row["asset_type"],
        geojson=geojson,
        bbox=_parse_bbox(row["bbox"]),
        style=style,
        metadata=metadata,
        visible=bool(row["visible"]),
        animated=bool(row["animated"]),
        z_index=row["z_index"] or 0,
        source_url=row["source_url"],
        animation=_parse_animation(row),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


# Every column except geojson — the summary query must not pull multi-MB
# geometry strings out of SQLite just to throw them away.
_SUMMARY_COLUMNS = (
    "id, map_id, name, asset_type, style, metadata, visible, animated, "
    "z_index, source_url, bbox, animation, created_at, updated_at"
)


async def list_assets(
    map_id: str, include_geojson: bool = True
) -> list[AssetResponse]:
    """List all assets for a map, bottom-most first (ascending z_index).

    Iterating the result and adding layers in order reproduces the stacking:
    later rows render on top (MapLibre adds new layers above existing ones).

    ``include_geojson=False`` returns a summary (geojson=None, bbox kept) —
    the shape layer-manager pollers should request every few seconds, since
    full geometries can be many MB per asset.
    """
    db = await get_db()
    columns = "*" if include_geojson else _SUMMARY_COLUMNS
    cursor = await db.execute(
        f"SELECT {columns} FROM assets WHERE map_id = ? ORDER BY z_index ASC, created_at ASC",
        (map_id,),
    )
    rows = await cursor.fetchall()
    return [
        _row_to_asset(row, row["geojson"] if include_geojson else None)
        for row in rows
    ]


async def get_asset(map_id: str, asset_id: str) -> AssetResponse | None:
    """Get a single asset."""
    db = await get_db()
    cursor = await db.execute(
        "SELECT * FROM assets WHERE id = ? AND map_id = ?", (asset_id, map_id)
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    return _row_to_asset(row, row["geojson"])


async def reorder_assets(map_id: str, asset_ids: list[str]) -> None:
    """Persist a new stacking order.

    ``asset_ids`` is ordered TOP-most first (matching a layer-manager list).
    Listed assets take the top of the stack in that order; any asset not
    mentioned keeps its relative order BELOW them. Every asset is renumbered
    0..n-1 (bottom first), so the stored order is total and matches what the
    live map does with the same list.
    """
    db = await get_db()
    order = await layer_order(map_id)
    listed = [aid for aid in dict.fromkeys(reversed(asset_ids)) if aid in order]
    rest = [aid for aid in order if aid not in set(listed)]
    await _write_order(map_id, rest + listed)


async def layer_order(map_id: str) -> list[str]:
    """Asset ids in stacking order, bottom-most first (the GET /assets order)."""
    db = await get_db()
    cursor = await db.execute(
        "SELECT id FROM assets WHERE map_id = ? ORDER BY z_index ASC, created_at ASC",
        (map_id,),
    )
    return [row["id"] for row in await cursor.fetchall()]


async def layer_z(map_id: str) -> dict[str, int]:
    """Every asset's stored z_index. Broadcast with each change that moves
    any asset, so live viewers adopt the server's numbers instead of keeping
    their own (a raster insert shifts every vector above it)."""
    db = await get_db()
    cursor = await db.execute(
        "SELECT id, z_index FROM assets WHERE map_id = ?", (map_id,)
    )
    return {row["id"]: row["z_index"] or 0 for row in await cursor.fetchall()}


async def _write_order(map_id: str, order: list[str]) -> None:
    db = await get_db()
    now = datetime.now(timezone.utc).isoformat()
    for z, aid in enumerate(order):
        await db.execute(
            "UPDATE assets SET z_index = ?, updated_at = ? WHERE id = ? AND map_id = ?",
            (z, now, aid, map_id),
        )
    await db.commit()


async def move_asset(map_id: str, asset_id: str, position: str) -> None:
    """Persist a single-asset move ('top' | 'bottom' | 'up' | 'down').

    Mirrors the live map's move_layer handling so the stored order survives
    reloads — move_layer used to be broadcast-only, and the next snapshot or
    layer-manager poll silently snapped the stack back.
    """
    order = await layer_order(map_id)
    if asset_id not in order:
        return
    idx = order.index(asset_id)
    order.pop(idx)
    if position == "top":
        order.append(asset_id)
    elif position == "bottom":
        order.insert(0, asset_id)
    elif position == "up":
        order.insert(min(idx + 1, len(order)), asset_id)
    elif position == "down":
        order.insert(max(idx - 1, 0), asset_id)
    else:
        return
    await _write_order(map_id, order)


async def update_asset(
    map_id: str, asset_id: str, update: AssetUpdate
) -> AssetResponse | None:
    """Partially update an asset."""
    db = await get_db()

    # Build SET clause dynamically
    updates = []
    params = []

    if update.name is not None:
        updates.append("name = ?")
        params.append(update.name)
    if update.style is not None:
        # Merge, never replace: live viewers apply a partial style on top of
        # what they draw, so replacing here made a reload lose every key the
        # caller didn't resend.
        cursor = await db.execute(
            "SELECT style FROM assets WHERE id = ? AND map_id = ?", (asset_id, map_id)
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        stored = AssetStyle.model_validate_json(row["style"]) if row["style"] else None
        updates.append("style = ?")
        params.append(merge_style(stored, update.style).model_dump_json(exclude_none=True))
    if update.visible is not None:
        updates.append("visible = ?")
        params.append(int(update.visible))
    if update.animated is not None:
        updates.append("animated = ?")
        params.append(int(update.animated))

    if not updates and update.metadata is None:
        return await get_asset(map_id, asset_id)

    while True:
        set_parts = list(updates)
        values = list(params)
        metadata_before = None
        if update.metadata is not None:
            # Read only metadata, not potentially multi-MB geometry. Each
            # extra key is a namespace replaced as a whole. Guard the write
            # with the exact stored JSON so concurrent metadata edits retry
            # against the newest value instead of losing unrelated fields.
            cursor = await db.execute(
                "SELECT metadata FROM assets WHERE id = ? AND map_id = ?",
                (asset_id, map_id),
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            metadata_before = row["metadata"]
            metadata = json.loads(metadata_before) if metadata_before else {}
            patch = update.metadata.model_dump(exclude_unset=True)
            if "extra" in patch:
                patch["extra"] = {**metadata.get("extra", {}), **patch["extra"]}
            metadata.update(patch)
            set_parts.append("metadata = ?")
            values.append(AssetMetadata.model_validate(metadata).model_dump_json())

        set_parts.append("updated_at = ?")
        values.append(datetime.now(timezone.utc).isoformat())
        values.extend([asset_id, map_id])
        where = "id = ? AND map_id = ?"
        if update.metadata is not None:
            # IS compares both SQL NULL and text, including legacy rows whose
            # metadata has not been written yet. No global/per-map lock needed.
            where += " AND metadata IS ?"
            values.append(metadata_before)
        cursor = await db.execute(
            f"UPDATE assets SET {', '.join(set_parts)} WHERE {where}", values
        )
        await db.commit()
        if update.metadata is None or cursor.rowcount:
            break
        # The row changed (or was deleted) between SELECT and UPDATE. Re-read
        # it and merge again; deletion returns None at the top of the loop.

    return await get_asset(map_id, asset_id)


async def delete_asset(map_id: str, asset_id: str) -> bool:
    """Delete an asset."""
    db = await get_db()
    cursor = await db.execute(
        "DELETE FROM assets WHERE id = ? AND map_id = ?", (asset_id, map_id)
    )
    await db.commit()
    return cursor.rowcount > 0
