"""Tests for map CRUD endpoints."""

import os
import tempfile

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from mapcontrol_server.main import app, lifespan


@pytest_asyncio.fixture
async def client():
    """Create a test client with a fresh database."""
    tmp = tempfile.mkdtemp()
    os.environ["MAPCONTROL_DB_PATH"] = f"{tmp}/test.db"
    os.environ["MAPCONTROL_FILE_DIR"] = f"{tmp}/files"

    async with lifespan(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


async def test_create_map(client):
    resp = await client.post("/api/maps")
    assert resp.status_code == 201
    data = resp.json()
    assert "map_id" in data
    assert "url" in data
    assert data["url"].endswith(f"/map/{data['map_id']}")


async def test_get_map(client):
    create = await client.post("/api/maps")
    map_id = create.json()["map_id"]

    resp = await client.get(f"/api/maps/{map_id}")
    assert resp.status_code == 200
    assert resp.json()["map_id"] == map_id


async def test_get_map_not_found(client):
    resp = await client.get("/api/maps/nonexistent")
    assert resp.status_code == 404


async def test_delete_map(client):
    create = await client.post("/api/maps")
    map_id = create.json()["map_id"]

    resp = await client.delete(f"/api/maps/{map_id}")
    assert resp.status_code == 204

    resp = await client.get(f"/api/maps/{map_id}")
    assert resp.status_code == 404
