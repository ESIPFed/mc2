"""Asset CRUD operations and URL fetching."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import httpx

from ..database import get_db
from ..models import AssetResponse, AssetStyle, AssetMetadata, AssetUpdate


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
) -> AssetResponse:
    """Create a new asset on a map."""
    db = await get_db()
    if asset_id is None:
        asset_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    style_json = style.model_dump_json() if style else None
    metadata_json = metadata.model_dump_json() if metadata else None
    bbox = _compute_bbox(geojson)

    # New assets stack on top: z_index = current max + 1 for this map.
    cursor = await db.execute(
        "SELECT COALESCE(MAX(z_index), -1) + 1 FROM assets WHERE map_id = ?",
        (map_id,),
    )
    row = await cursor.fetchone()
    z_index = row[0] if row else 0

    await db.execute(
        """INSERT INTO assets (id, map_id, name, asset_type, geojson, style, metadata, visible, animated, z_index, source_url, bbox, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?)""",
        (
            asset_id,
            map_id,
            name,
            asset_type,
            geojson,
            style_json,
            metadata_json,
            int(animated),
            z_index,
            source_url,
            json.dumps(bbox) if bbox else None,
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
        visible=True,
        animated=animated,
        z_index=z_index,
        source_url=source_url,
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
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


# Every column except geojson — the summary query must not pull multi-MB
# geometry strings out of SQLite just to throw them away.
_SUMMARY_COLUMNS = (
    "id, map_id, name, asset_type, style, metadata, visible, animated, "
    "z_index, source_url, bbox, created_at, updated_at"
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
    The first id gets the highest z_index. Assets not mentioned keep their
    existing z_index (they end up below the reordered set).
    """
    db = await get_db()
    now = datetime.now(timezone.utc).isoformat()
    n = len(asset_ids)
    for i, aid in enumerate(asset_ids):
        await db.execute(
            "UPDATE assets SET z_index = ?, updated_at = ? WHERE id = ? AND map_id = ?",
            (n - i, now, aid, map_id),
        )
    await db.commit()


async def update_asset(
    map_id: str, asset_id: str, update: AssetUpdate
) -> AssetResponse | None:
    """Partially update an asset."""
    db = await get_db()
    now = datetime.now(timezone.utc).isoformat()

    # Build SET clause dynamically
    updates = []
    params = []

    if update.name is not None:
        updates.append("name = ?")
        params.append(update.name)
    if update.style is not None:
        updates.append("style = ?")
        params.append(update.style.model_dump_json())
    if update.metadata is not None:
        updates.append("metadata = ?")
        params.append(update.metadata.model_dump_json())
    if update.visible is not None:
        updates.append("visible = ?")
        params.append(int(update.visible))
    if update.animated is not None:
        updates.append("animated = ?")
        params.append(int(update.animated))

    if not updates:
        return await get_asset(map_id, asset_id)

    updates.append("updated_at = ?")
    params.append(now)
    params.extend([asset_id, map_id])

    set_clause = ", ".join(updates)
    await db.execute(
        f"UPDATE assets SET {set_clause} WHERE id = ? AND map_id = ?", params
    )
    await db.commit()

    return await get_asset(map_id, asset_id)


async def delete_asset(map_id: str, asset_id: str) -> bool:
    """Delete an asset."""
    db = await get_db()
    cursor = await db.execute(
        "DELETE FROM assets WHERE id = ? AND map_id = ?", (asset_id, map_id)
    )
    await db.commit()
    return cursor.rowcount > 0
