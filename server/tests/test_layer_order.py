"""Stacking-order tests (issue #111): the DB z_index is the canonical order.

The live map, layer managers, and session restore all derive their stacking
from ascending z_index — these tests pin the server-side policy:
  - vectors stack on top (max + 1);
  - rasters (tile layers / geotiffs) slot in just below the lowest vector;
  - reorder_assets and move_layer both persist.
"""

import os
import tempfile

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


async def _add_polygon(client, map_id, name):
    resp = await client.post(f"/api/maps/{map_id}/events", json={
        "type": "add_polygon",
        "data": {"geojson": SAMPLE_GEOJSON, "name": name},
    })
    return resp.json()["asset_id"]


async def _add_tile_layer(client, map_id, name):
    resp = await client.post(f"/api/maps/{map_id}/events", json={
        "type": "add_tile_layer",
        "data": {"url": "https://tile.example/{z}/{x}/{y}.png", "name": name},
    })
    return resp.json()["asset_id"]


async def _order(client, map_id):
    """Asset ids bottom-most first (the documented list order)."""
    resp = await client.get(f"/api/maps/{map_id}/assets?include_geojson=false")
    assert resp.status_code == 200
    return [a["asset_id"] for a in resp.json()]


@pytest_asyncio.fixture
async def map_id(client):
    resp = await client.post("/api/maps")
    return resp.json()["map_id"]


async def test_vectors_stack_on_top(client, map_id):
    a = await _add_polygon(client, map_id, "A")
    b = await _add_polygon(client, map_id, "B")
    assert await _order(client, map_id) == [a, b]


async def test_raster_slots_below_vectors(client, map_id):
    a = await _add_polygon(client, map_id, "A")
    b = await _add_polygon(client, map_id, "B")
    t = await _add_tile_layer(client, map_id, "T")
    # The tile layer lands BELOW both polygons even though it was added last.
    assert await _order(client, map_id) == [t, a, b]


async def test_raster_on_empty_map_is_bottom_of_later_vectors(client, map_id):
    t = await _add_tile_layer(client, map_id, "T")
    a = await _add_polygon(client, map_id, "A")
    assert await _order(client, map_id) == [t, a]


async def test_second_raster_lands_above_first_but_below_vectors(client, map_id):
    t1 = await _add_tile_layer(client, map_id, "T1")
    a = await _add_polygon(client, map_id, "A")
    t2 = await _add_tile_layer(client, map_id, "T2")
    assert await _order(client, map_id) == [t1, t2, a]


async def test_reorder_assets_persists(client, map_id):
    a = await _add_polygon(client, map_id, "A")
    b = await _add_polygon(client, map_id, "B")
    t = await _add_tile_layer(client, map_id, "T")
    # Layer-manager order is TOP-most first; put the raster on top.
    resp = await client.post(f"/api/maps/{map_id}/events", json={
        "type": "reorder_assets",
        "data": {"asset_ids": [t, b, a]},
    })
    assert resp.status_code == 200
    assert await _order(client, map_id) == [a, b, t]


async def test_reorder_survives_new_raster(client, map_id):
    """An explicit raster-on-top order stays put; a NEW raster still slots
    below the vectors (initial-placement rule only applies to new assets)."""
    a = await _add_polygon(client, map_id, "A")
    t = await _add_tile_layer(client, map_id, "T")
    await client.post(f"/api/maps/{map_id}/events", json={
        "type": "reorder_assets",
        "data": {"asset_ids": [t, a]},  # user puts the raster on top
    })
    t2 = await _add_tile_layer(client, map_id, "T2")
    assert await _order(client, map_id) == [t2, a, t]


async def test_move_layer_persists(client, map_id):
    a = await _add_polygon(client, map_id, "A")
    b = await _add_polygon(client, map_id, "B")
    c = await _add_polygon(client, map_id, "C")

    resp = await client.post(f"/api/maps/{map_id}/events", json={
        "type": "move_layer",
        "data": {"asset_id": a, "position": "top"},
    })
    assert resp.status_code == 200
    assert await _order(client, map_id) == [b, c, a]

    await client.post(f"/api/maps/{map_id}/events", json={
        "type": "move_layer",
        "data": {"asset_id": a, "position": "down"},
    })
    assert await _order(client, map_id) == [b, a, c]

    await client.post(f"/api/maps/{map_id}/events", json={
        "type": "move_layer",
        "data": {"asset_id": c, "position": "bottom"},
    })
    assert await _order(client, map_id) == [c, b, a]

    await client.post(f"/api/maps/{map_id}/events", json={
        "type": "move_layer",
        "data": {"asset_id": c, "position": "up"},
    })
    assert await _order(client, map_id) == [b, c, a]


async def test_add_polygon_asset_carries_z_index(client, map_id):
    a = await _add_polygon(client, map_id, "A")
    b = await _add_polygon(client, map_id, "B")
    ra = await client.get(f"/api/maps/{map_id}/assets/{a}")
    rb = await client.get(f"/api/maps/{map_id}/assets/{b}")
    assert rb.json()["z_index"] > ra.json()["z_index"]
