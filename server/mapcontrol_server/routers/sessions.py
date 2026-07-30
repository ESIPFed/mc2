"""User session endpoints."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request

from ..models import SessionCreateResponse, ViewportResponse, Viewport
from ..services import session_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/maps/{map_id}/sessions", tags=["sessions"])


def _base_url(request: Request) -> str:
    return str(request.base_url).rstrip("/")


@router.post("", response_model=SessionCreateResponse, status_code=201)
async def create_session(map_id: str, request: Request):
    """Create a user session for a map."""
    result = await session_service.create_session(map_id, _base_url(request))
    if result is None:
        raise HTTPException(status_code=404, detail="Map not found")
    return result


@router.get("/{user_session_id}/viewport", response_model=ViewportResponse)
async def get_viewport(map_id: str, user_session_id: str):
    """Get the current viewport and contained assets for a user session."""
    snapshot = await session_service.get_session_snapshot(map_id, user_session_id)

    # Determine which assets are in the viewport (if viewport is known)
    contained = []
    if snapshot.viewport and snapshot.viewport.bbox:
        # For now, return all asset IDs — spatial filtering can come later
        contained = [a.asset_id for a in snapshot.assets if a.visible]

    return ViewportResponse(
        viewport=snapshot.viewport,
        basemap=snapshot.basemap,
        contained_assets=contained,
    )
