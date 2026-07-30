"""Asset CRUD endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..models import AssetResponse, AssetUpdate
from ..services import asset_service, session_service

router = APIRouter(prefix="/api/maps/{map_id}/assets", tags=["assets"])


@router.get("", response_model=list[AssetResponse])
async def list_assets(map_id: str):
    """List all assets for a map."""
    info = await session_service.get_map(map_id)
    if info is None:
        raise HTTPException(status_code=404, detail="Map not found")

    return await asset_service.list_assets(map_id)


@router.get("/{asset_id}", response_model=AssetResponse)
async def get_asset(map_id: str, asset_id: str):
    """Get a single asset."""
    asset = await asset_service.get_asset(map_id, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    return asset


@router.patch("/{asset_id}", response_model=AssetResponse)
async def update_asset(map_id: str, asset_id: str, update: AssetUpdate):
    """Update an asset (style, visibility, metadata)."""
    asset = await asset_service.update_asset(map_id, asset_id, update)
    if asset is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    return asset


@router.delete("/{asset_id}", status_code=204)
async def delete_asset(map_id: str, asset_id: str):
    """Delete an asset."""
    deleted = await asset_service.delete_asset(map_id, asset_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Asset not found")
