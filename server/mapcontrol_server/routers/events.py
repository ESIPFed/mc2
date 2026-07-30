"""Map event endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from ..models import MapEvent, MapEventResponse, EventListItem
from ..services import event_service, session_service

router = APIRouter(prefix="/api/maps/{map_id}/events", tags=["events"])


@router.post("", response_model=MapEventResponse, status_code=201)
async def post_event(map_id: str, event: MapEvent):
    """Send a map event/command."""
    # Verify map exists
    info = await session_service.get_map(map_id)
    if info is None:
        raise HTTPException(status_code=404, detail="Map not found")

    result = await event_service.process_event(map_id, event)
    return result


@router.get("", response_model=list[EventListItem])
async def list_events(
    map_id: str,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
):
    """List events for a map with pagination."""
    info = await session_service.get_map(map_id)
    if info is None:
        raise HTTPException(status_code=404, detail="Map not found")

    return await event_service.list_events(map_id, limit=limit, offset=offset)
