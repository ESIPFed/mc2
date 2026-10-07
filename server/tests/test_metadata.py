"""Metadata updates persist and reach already-connected map viewers."""

import asyncio
import json
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from mapcontrol_server.main import app
from mapcontrol_server.config import load_config
from mapcontrol_server.database import init_db, close_db
from mapcontrol_server.services import session_service
from mapcontrol_server.websocket import manager


POINT = '{"type":"Point","coordinates":[-97.7,30.2]}'
INITIAL = {
    "title": "Observation site",
    "description": "Original description",
    "extra": {
        "source": {"id": "source-123"},
        "inspector": {"version": 1, "status": "loading", "attachments": []},
    },
}
READY = {
    "version": 1,
    "status": "ready",
    "attachments": [{"type": "html", "url": "https://example.org/chart.html?signature=a%2Fb"}],
}


@pytest_asyncio.fixture
async def map_client(tmp_path, monkeypatch):
    monkeypatch.setenv("MAPCONTROL_DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("MAPCONTROL_FILE_DIR", str(tmp_path / "files"))
    # Asset routes need SQLite, not the MCP manager's single-use lifespan.
    await init_db(load_config())
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            map_id = (await client.post("/api/maps")).json()["map_id"]
            result = await client.post(f"/api/maps/{map_id}/events", json={
                "type": "add_point", "data": {"geojson": POINT, "metadata": INITIAL},
            })
            assert result.status_code == 201
            socket = AsyncMock()
            await manager.connect(socket, map_id, "viewer-session")
            try:
                yield client, map_id, result.json()["asset_id"], socket
            finally:
                manager.disconnect(socket, map_id)
    finally:
        await close_db()


async def test_update_metadata_persists_merges_and_broadcasts(map_client):
    client, map_id, asset_id, socket = map_client
    response = await client.post(f"/api/maps/{map_id}/events", json={
        "type": "update_metadata",
        "data": {"asset_id": asset_id, "metadata": {"extra": {"inspector": READY}}},
    })
    assert response.status_code == 201
    assert response.json()["error"] is None
    assert response.json()["asset_id"] == asset_id
    expected = {**INITIAL, "extra": {**INITIAL["extra"], "inspector": READY}}
    asset = (await client.get(f"/api/maps/{map_id}/assets/{asset_id}")).json()
    assert asset["metadata"] == expected
    socket.send_text.assert_awaited_once()
    message = json.loads(socket.send_text.call_args.args[0])
    assert message["type"] == "update_metadata"
    assert message["data"] == {"asset_id": asset_id, "metadata": expected}

    # A newly connected viewer restores the same metadata from storage.
    snapshot = await session_service.get_session_snapshot(map_id, "new-viewer")
    assert snapshot.assets[0].metadata.model_dump() == expected


async def test_concurrent_metadata_namespaces_are_preserved(map_client):
    client, map_id, asset_id, _ = map_client
    namespaces = {f"attachment-{i}": {"value": i} for i in range(12)}
    responses = await asyncio.gather(*(
        client.patch(f"/api/maps/{map_id}/assets/{asset_id}", json={
            "metadata": {"extra": {key: value}},
        })
        for key, value in namespaces.items()
    ))
    assert all(response.status_code == 200 for response in responses)
    stored = (await client.get(f"/api/maps/{map_id}/assets/{asset_id}")).json()
    assert stored["metadata"] == {
        **INITIAL, "extra": {**INITIAL["extra"], **namespaces},
    }


async def test_concurrent_title_and_inspector_updates_are_preserved(map_client):
    client, map_id, asset_id, _ = map_client
    title_response, inspector_response = await asyncio.gather(
        client.patch(f"/api/maps/{map_id}/assets/{asset_id}", json={
            "metadata": {"title": "Updated observation site"},
        }),
        client.post(f"/api/maps/{map_id}/events", json={
            "type": "update_metadata",
            "data": {"asset_id": asset_id, "metadata": {"extra": {"inspector": READY}}},
        }),
    )
    assert title_response.status_code == 200
    assert inspector_response.status_code == 201
    assert inspector_response.json()["error"] is None
    stored = (await client.get(f"/api/maps/{map_id}/assets/{asset_id}")).json()
    assert stored["metadata"] == {
        **INITIAL,
        "title": "Updated observation site",
        "extra": {**INITIAL["extra"], "inspector": READY},
    }


async def test_rest_patch_replaces_inspector_namespace_and_notifies(map_client):
    client, map_id, asset_id, socket = map_client
    patch = {"description": "Updated description", "extra": {"inspector": {
        "version": 1, "attachments": [{"type": "image", "url": "/files/observation.png"}],
    }}}
    response = await client.patch(f"/api/maps/{map_id}/assets/{asset_id}", json={"metadata": patch})
    assert response.status_code == 200
    updated = response.json()
    assert updated["metadata"]["title"] == INITIAL["title"]
    assert updated["metadata"]["description"] == patch["description"]
    assert updated["metadata"]["extra"]["source"] == INITIAL["extra"]["source"]
    assert updated["metadata"]["extra"]["inspector"] == patch["extra"]["inspector"]
    assert "status" not in updated["metadata"]["extra"]["inspector"]
    socket.send_text.assert_awaited_once()
    message = json.loads(socket.send_text.call_args.args[0])
    assert message == {"type": "asset_updated", "data": updated}


async def test_metadata_can_clear_text_and_disable_inspector(map_client):
    client, map_id, asset_id, _ = map_client
    response = await client.patch(f"/api/maps/{map_id}/assets/{asset_id}", json={
        "metadata": {"title": None, "description": "", "extra": {"inspector": None}},
    })
    assert response.status_code == 200
    metadata = response.json()["metadata"]
    assert metadata["title"] is None
    assert metadata["description"] == ""
    assert metadata["extra"] == {"source": INITIAL["extra"]["source"], "inspector": None}


@pytest.mark.parametrize("event_type", ["add_point", "add_points", "update_metadata", "rest_patch"])
async def test_status_only_inspector_normalizes_on_write_and_broadcast(map_client, event_type):
    client, map_id, asset_id, socket = map_client
    metadata = {"extra": {"source": {"id": "retained"}, "inspector": {"status": "loading"}}}
    expected_extra = {
        "source": {"id": "retained"},
        "inspector": {"version": 1, "status": "loading", "attachments": []},
    }
    if event_type == "rest_patch":
        result = await client.patch(f"/api/maps/{map_id}/assets/{asset_id}", json={"metadata": metadata})
        assert result.status_code == 200
    else:
        data = {"asset_id": asset_id, "metadata": metadata, "geojson": POINT}
        if event_type == "add_points":
            data = {"items": [data]}
        result = await client.post(f"/api/maps/{map_id}/events", json={"type": event_type, "data": data})
        assert result.status_code == 201
        assert result.json()["error"] is None
        asset_id = result.json()["asset_id"]
    stored = (await client.get(f"/api/maps/{map_id}/assets/{asset_id}")).json()
    assert stored["metadata"]["extra"] == expected_extra
    socket.send_text.assert_awaited_once()
    message = json.loads(socket.send_text.call_args.args[0])
    assert message["data"]["metadata"]["extra"] == expected_extra


async def test_rest_delete_notifies_viewers_and_missing_asset_does_not(map_client):
    client, map_id, asset_id, socket = map_client
    response = await client.delete(f"/api/maps/{map_id}/assets/{asset_id}")
    assert response.status_code == 204
    socket.send_text.assert_awaited_once()
    assert json.loads(socket.send_text.call_args.args[0]) == {
        "type": "delete_asset", "data": {"asset_id": asset_id},
    }
    socket.reset_mock()
    response = await client.delete(f"/api/maps/{map_id}/assets/{asset_id}")
    assert response.status_code == 404
    response = await client.patch(f"/api/maps/{map_id}/assets/{asset_id}", json={"metadata": INITIAL})
    assert response.status_code == 404
    socket.send_text.assert_not_awaited()


@pytest.mark.parametrize("data,error", [
    ({"asset_id": "missing", "metadata": {}}, "Asset not found"),
    ({"metadata": {}}, "requires"),
    ({"asset_id": "current", "metadata": None}, "requires"),
    ({"asset_id": "current", "metadata": {"extra": {"inspector": {"version": 2}}}}, "Invalid metadata"),
])
async def test_invalid_update_does_not_mutate_or_broadcast(map_client, data, error):
    client, map_id, asset_id, socket = map_client
    if data.get("asset_id") == "current":
        data = {**data, "asset_id": asset_id}
    response = await client.post(f"/api/maps/{map_id}/events", json={"type": "update_metadata", "data": data})
    assert error in response.json()["error"]
    socket.send_text.assert_not_awaited()
    asset = (await client.get(f"/api/maps/{map_id}/assets/{asset_id}")).json()
    assert asset["metadata"] == INITIAL


@pytest.mark.parametrize("url", ["javascript:alert(1)", "data:text/html,test", "file:///tmp/chart.html", "https:///broken", "https://example.org/\nchart"])
async def test_invalid_attachment_urls_rejected_before_persisting(map_client, url):
    client, map_id, asset_id, socket = map_client
    metadata = {"extra": {"inspector": {"version": 1, "attachments": [{"type": "html", "url": url}]}}}
    response = await client.patch(f"/api/maps/{map_id}/assets/{asset_id}", json={"metadata": metadata})
    assert response.status_code == 422
    response = await client.post(f"/api/maps/{map_id}/events", json={
        "type": "update_metadata", "data": {"asset_id": asset_id, "metadata": metadata},
    })
    assert "Invalid metadata" in response.json()["error"]
    socket.send_text.assert_not_awaited()


async def test_batch_creation_broadcasts_inspector_metadata(map_client):
    client, map_id, _, socket = map_client
    response = await client.post(f"/api/maps/{map_id}/events", json={
        "type": "add_points", "data": {"items": [{"geojson": POINT, "metadata": INITIAL}]},
    })
    assert response.status_code == 201
    socket.send_text.assert_awaited_once()
    message = json.loads(socket.send_text.call_args.args[0])
    assert message["type"] == "add_point"
    assert message["data"]["metadata"] == INITIAL


@pytest.mark.parametrize("event_type", ["add_point", "add_points"])
async def test_invalid_create_metadata_leaves_no_assets_or_events(map_client, event_type):
    client, map_id, _, socket = map_client
    invalid = {"extra": {"inspector": {"attachments": [{
        "type": "html", "url": "javascript:SECRET_TOKEN",
    }]}}}
    data = {"geojson": POINT, "metadata": invalid}
    if event_type == "add_points":
        data = {"items": [{"geojson": POINT, "metadata": INITIAL}, data]}
    response = await client.post(f"/api/maps/{map_id}/events", json={"type": event_type, "data": data})
    assert response.status_code == 201
    assert "Invalid metadata" in response.json()["error"]
    assert "SECRET_TOKEN" not in response.text
    assert len((await client.get(f"/api/maps/{map_id}/assets")).json()) == 1
    assert len((await client.get(f"/api/maps/{map_id}/events")).json()) == 1
    socket.send_text.assert_not_awaited()
