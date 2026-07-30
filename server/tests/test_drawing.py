"""Tests for drawing-related events and asset creation."""

import os
import tempfile

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from mapcontrol_server.main import app, lifespan

DRAWN_POLYGON_GEOJSON = '{"type":"Feature","geometry":{"type":"Polygon","coordinates":[[[-97.7,30.2],[-97.7,30.3],[-97.6,30.3],[-97.6,30.2],[-97.7,30.2]]]},"properties":{}}'
DRAWN_BOX_GEOJSON = '{"type":"Feature","geometry":{"type":"Polygon","coordinates":[[[-97.8,30.1],[-97.8,30.4],[-97.5,30.4],[-97.5,30.1],[-97.8,30.1]]]},"properties":{}}'


@pytest_asyncio.fixture
async def client():
    tmp = tempfile.mkdtemp()
    os.environ["MAPCONTROL_DB_PATH"] = f"{tmp}/test.db"
    os.environ["MAPCONTROL_FILE_DIR"] = f"{tmp}/files"
    async with lifespan(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


@pytest_asyncio.fixture
async def map_id(client):
    resp = await client.post("/api/maps")
    return resp.json()["map_id"]


@pytest_asyncio.fixture
async def session_id(client, map_id):
    resp = await client.post(f"/api/maps/{map_id}/sessions")
    return resp.json()["user_session_id"]


async def test_enable_drawing_event(client, map_id, session_id):
    """enable_drawing should be accepted as a valid event."""
    resp = await client.post(f"/api/maps/{map_id}/events", json={
        "type": "enable_drawing",
        "data": {"mode": "polygon"},
        "user_session_id": session_id,
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["type"] == "enable_drawing"


async def test_enable_drawing_box_mode(client, map_id, session_id):
    """enable_drawing with box mode should be accepted."""
    resp = await client.post(f"/api/maps/{map_id}/events", json={
        "type": "enable_drawing",
        "data": {"mode": "box"},
        "user_session_id": session_id,
    })
    assert resp.status_code == 201
    assert resp.json()["type"] == "enable_drawing"


async def test_disable_drawing_event(client, map_id, session_id):
    """disable_drawing should be accepted as a valid event."""
    resp = await client.post(f"/api/maps/{map_id}/events", json={
        "type": "disable_drawing",
        "data": {},
        "user_session_id": session_id,
    })
    assert resp.status_code == 201
    assert resp.json()["type"] == "disable_drawing"


async def test_drawn_asset_created_via_direct_api(client, map_id):
    """Simulate what the WebSocket handler does: create a drawn asset directly."""
    from mapcontrol_server.services import asset_service
    from mapcontrol_server.models import AssetStyle

    style = AssetStyle(
        fill_color="#4264fb",
        stroke_color="#4264fb",
        stroke_width=2.0,
    )

    asset = await asset_service.create_asset(
        map_id=map_id,
        asset_type="drawn_polygon",
        geojson=DRAWN_POLYGON_GEOJSON,
        name="User drawn polygon",
        style=style,
    )

    assert asset.asset_id is not None
    assert asset.asset_type == "drawn_polygon"
    assert asset.name == "User drawn polygon"

    # Verify it shows up in the asset list
    assets = await asset_service.list_assets(map_id)
    drawn_assets = [a for a in assets if a.asset_type.startswith("drawn_")]
    assert len(drawn_assets) == 1
    assert drawn_assets[0].asset_id == asset.asset_id


async def test_drawn_box_asset(client, map_id):
    """Create a drawn_box asset and verify it persists."""
    from mapcontrol_server.services import asset_service
    from mapcontrol_server.models import AssetStyle

    style = AssetStyle(
        fill_color="#4264fb",
        stroke_color="#4264fb",
        stroke_width=2.0,
    )

    asset = await asset_service.create_asset(
        map_id=map_id,
        asset_type="drawn_box",
        geojson=DRAWN_BOX_GEOJSON,
        name="User drawn box",
        style=style,
    )

    assert asset.asset_type == "drawn_box"

    # Retrieve via API
    resp = await client.get(f"/api/maps/{map_id}/assets/{asset.asset_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["asset_type"] == "drawn_box"
    assert data["name"] == "User drawn box"


async def test_drawn_asset_appears_in_asset_list(client, map_id):
    """Drawn assets should appear alongside regular assets in list_assets."""
    from mapcontrol_server.services import asset_service
    from mapcontrol_server.models import AssetStyle

    # Add a regular polygon
    await client.post(f"/api/maps/{map_id}/events", json={
        "type": "add_polygon",
        "data": {"geojson": DRAWN_POLYGON_GEOJSON, "name": "Regular Polygon"},
    })

    # Add a drawn polygon directly
    await asset_service.create_asset(
        map_id=map_id,
        asset_type="drawn_polygon",
        geojson=DRAWN_BOX_GEOJSON,
        name="Drawn Polygon",
        style=AssetStyle(fill_color="#4264fb", stroke_color="#4264fb", stroke_width=2.0),
    )

    # List all assets
    resp = await client.get(f"/api/maps/{map_id}/assets")
    assert resp.status_code == 200
    assets = resp.json()
    assert len(assets) == 2

    types = {a["asset_type"] for a in assets}
    assert "polygon" in types
    assert "drawn_polygon" in types


async def test_drawn_asset_can_be_deleted(client, map_id):
    """Drawn assets should be deletable like any other asset."""
    from mapcontrol_server.services import asset_service
    from mapcontrol_server.models import AssetStyle

    asset = await asset_service.create_asset(
        map_id=map_id,
        asset_type="drawn_polygon",
        geojson=DRAWN_POLYGON_GEOJSON,
        name="To Delete",
        style=AssetStyle(fill_color="#4264fb", stroke_color="#4264fb", stroke_width=2.0),
    )

    # Delete via event
    resp = await client.post(f"/api/maps/{map_id}/events", json={
        "type": "delete_asset",
        "data": {"asset_id": asset.asset_id},
    })
    assert resp.status_code == 201

    # Verify it is gone
    resp = await client.get(f"/api/maps/{map_id}/assets/{asset.asset_id}")
    assert resp.status_code == 404


async def test_drawn_asset_style_can_be_updated(client, map_id):
    """Drawn assets should support style updates."""
    from mapcontrol_server.services import asset_service
    from mapcontrol_server.models import AssetStyle

    asset = await asset_service.create_asset(
        map_id=map_id,
        asset_type="drawn_polygon",
        geojson=DRAWN_POLYGON_GEOJSON,
        name="Styled Draw",
        style=AssetStyle(fill_color="#4264fb", stroke_color="#4264fb", stroke_width=2.0),
    )

    # Update style via event
    resp = await client.post(f"/api/maps/{map_id}/events", json={
        "type": "update_style",
        "data": {
            "asset_id": asset.asset_id,
            "style": {"fill_color": "#ff0000", "stroke_color": "#ff0000"},
        },
    })
    assert resp.status_code == 201

    # Verify updated style
    resp = await client.get(f"/api/maps/{map_id}/assets/{asset.asset_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["style"]["fill_color"] == "#ff0000"
