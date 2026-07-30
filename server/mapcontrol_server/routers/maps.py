"""Map CRUD endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from ..models import MapCreateResponse, MapInfo
from ..services import session_service

router = APIRouter(prefix="/api/maps", tags=["maps"])


def _base_url(request: Request) -> str:
    """Construct the base URL from the request."""
    return str(request.base_url).rstrip("/")


@router.post("", response_model=MapCreateResponse, status_code=201)
async def create_map(request: Request):
    """Create a new map workspace.

    Optional JSON body: ``{"theme": "light" | "dark" | "auto", "basemap": "<key>"}``
    — the map-level UI theme ('auto' default: follows the viewer's OS
    preference) and an optional pinned starting basemap.
    """
    theme = "auto"
    basemap = None
    try:
        body = await request.json()
        if isinstance(body, dict):
            theme = body.get("theme", "auto")
            basemap = body.get("basemap")
    except Exception:
        pass  # empty/non-JSON body → defaults
    result = await session_service.create_map(
        _base_url(request), theme=theme, basemap=basemap
    )
    return result


@router.get("/{map_id}", response_model=MapInfo)
async def get_map(map_id: str):
    """Get map info."""
    info = await session_service.get_map(map_id)
    if info is None:
        raise HTTPException(status_code=404, detail="Map not found")
    return info


@router.delete("/{map_id}", status_code=204)
async def delete_map(map_id: str):
    """Delete a map and all its data."""
    deleted = await session_service.delete_map(map_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Map not found")
