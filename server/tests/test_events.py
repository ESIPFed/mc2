"""Tests for map event endpoints."""

import os
import tempfile

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from mapcontrol_server.main import app, lifespan

SAMPLE_GEOJSON = '{"type":"Feature","geometry":{"type":"Polygon","coordinates":[[[-97.7,30.2],[-97.7,30.3],[-97.6,30.3],[-97.6,30.2],[-97.7,30.2]]]},"properties":{}}'


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


async def test_add_polygon(client, map_id):
    resp = await client.post(f"/api/maps/{map_id}/events", json={
        "type": "add_polygon",
        "data": {
            "geojson": SAMPLE_GEOJSON,
            "name": "Test Polygon",
            "style": {"fill_color": "#ff0000", "stroke_color": "#000000"},
        }
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["type"] == "add_polygon"
    assert data["asset_id"] is not None


async def test_zoom_to_point(client, map_id):
    resp = await client.post(f"/api/maps/{map_id}/events", json={
        "type": "zoom_to_point",
        "data": {"lon": -97.7, "lat": 30.2, "zoom": 14}
    })
    assert resp.status_code == 201
    assert resp.json()["type"] == "zoom_to_point"


async def test_list_events(client, map_id):
    # Create some events
    await client.post(f"/api/maps/{map_id}/events", json={
        "type": "zoom_to_point",
        "data": {"lon": -97.7, "lat": 30.2}
    })
    await client.post(f"/api/maps/{map_id}/events", json={
        "type": "add_polygon",
        "data": {"geojson": SAMPLE_GEOJSON}
    })

    resp = await client.get(f"/api/maps/{map_id}/events")
    assert resp.status_code == 200
    events = resp.json()
    assert len(events) == 2


async def test_delete_asset_via_event(client, map_id):
    # Add polygon
    add_resp = await client.post(f"/api/maps/{map_id}/events", json={
        "type": "add_polygon",
        "data": {"geojson": SAMPLE_GEOJSON, "name": "ToDelete"}
    })
    asset_id = add_resp.json()["asset_id"]

    # Delete via event
    del_resp = await client.post(f"/api/maps/{map_id}/events", json={
        "type": "delete_asset",
        "data": {"asset_id": asset_id}
    })
    assert del_resp.status_code == 201

    # Verify asset is gone
    asset_resp = await client.get(f"/api/maps/{map_id}/assets/{asset_id}")
    assert asset_resp.status_code == 404
