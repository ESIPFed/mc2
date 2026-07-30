"""Tests for asset CRUD endpoints."""

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
async def map_with_asset(client):
    """Create a map with one polygon asset."""
    map_resp = await client.post("/api/maps")
    map_id = map_resp.json()["map_id"]

    event_resp = await client.post(f"/api/maps/{map_id}/events", json={
        "type": "add_polygon",
        "data": {
            "geojson": SAMPLE_GEOJSON,
            "name": "Test Polygon",
            "style": {"fill_color": "#ff0000"},
        }
    })
    asset_id = event_resp.json()["asset_id"]
    return map_id, asset_id


async def test_list_assets(client, map_with_asset):
    map_id, asset_id = map_with_asset
    resp = await client.get(f"/api/maps/{map_id}/assets")
    assert resp.status_code == 200
    assets = resp.json()
    assert len(assets) == 1
    assert assets[0]["asset_id"] == asset_id


async def test_get_asset(client, map_with_asset):
    map_id, asset_id = map_with_asset
    resp = await client.get(f"/api/maps/{map_id}/assets/{asset_id}")
    assert resp.status_code == 200
    assert resp.json()["name"] == "Test Polygon"


async def test_update_asset_visibility(client, map_with_asset):
    map_id, asset_id = map_with_asset
    resp = await client.patch(f"/api/maps/{map_id}/assets/{asset_id}", json={
        "visible": False
    })
    assert resp.status_code == 200
    assert resp.json()["visible"] is False


async def test_update_asset_style(client, map_with_asset):
    map_id, asset_id = map_with_asset
    resp = await client.patch(f"/api/maps/{map_id}/assets/{asset_id}", json={
        "style": {"fill_color": "#00ff00", "stroke_color": "#000000"}
    })
    assert resp.status_code == 200
    assert resp.json()["style"]["fill_color"] == "#00ff00"


async def test_delete_asset(client, map_with_asset):
    map_id, asset_id = map_with_asset
    resp = await client.delete(f"/api/maps/{map_id}/assets/{asset_id}")
    assert resp.status_code == 204

    resp = await client.get(f"/api/maps/{map_id}/assets/{asset_id}")
    assert resp.status_code == 404
