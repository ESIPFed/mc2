"""Asset CRUD operations and URL fetching."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import httpx

from ..database import get_db
from ..models import AssetResponse, AssetStyle, AssetMetadata, AssetUpdate


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

    await db.execute(
        """INSERT INTO assets (id, map_id, name, asset_type, geojson, style, metadata, visible, animated, source_url, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)""",
        (
            asset_id,
            map_id,
            name,
            asset_type,
            geojson,
            style_json,
            metadata_json,
            int(animated),
            source_url,
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
        style=style,
        metadata=metadata,
        visible=True,
        animated=animated,
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


async def list_assets(map_id: str) -> list[AssetResponse]:
    """List all assets for a map."""
    db = await get_db()
    cursor = await db.execute(
        "SELECT * FROM assets WHERE map_id = ? ORDER BY created_at ASC", (map_id,)
    )
    rows = await cursor.fetchall()

    assets = []
    for row in rows:
        style = AssetStyle.model_validate_json(row["style"]) if row["style"] else None
        metadata = (
            AssetMetadata.model_validate_json(row["metadata"])
            if row["metadata"]
            else None
        )
        assets.append(
            AssetResponse(
                asset_id=row["id"],
                map_id=row["map_id"],
                name=row["name"],
                asset_type=row["asset_type"],
                geojson=row["geojson"],
                style=style,
                metadata=metadata,
                visible=bool(row["visible"]),
                animated=bool(row["animated"]),
                source_url=row["source_url"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
        )
    return assets


async def get_asset(map_id: str, asset_id: str) -> AssetResponse | None:
    """Get a single asset."""
    db = await get_db()
    cursor = await db.execute(
        "SELECT * FROM assets WHERE id = ? AND map_id = ?", (asset_id, map_id)
    )
    row = await cursor.fetchone()
    if row is None:
        return None

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
        geojson=row["geojson"],
        style=style,
        metadata=metadata,
        visible=bool(row["visible"]),
        animated=bool(row["animated"]),
        source_url=row["source_url"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


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
