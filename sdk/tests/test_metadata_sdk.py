"""The SDK sends partial metadata updates and reports server failures."""

import json

import httpx
import pytest

from mapcontrol import Metadata
from mapcontrol.exceptions import ServerError
from mapcontrol.session import MapSession


@pytest.mark.parametrize("metadata,expected", [
    (Metadata(description=""), {"description": ""}),
    (Metadata(extra={"inspector": {"version": 1, "status": "loading"}}),
     {"extra": {"inspector": {"version": 1, "status": "loading"}}}),
    ({"title": None, "extra": {"inspector": None}}, {"title": None, "extra": {"inspector": None}}),
])
def test_metadata_update_serializes_only_supplied_fields(metadata, expected):
    def handle(request):
        assert request.url.path == "/api/maps/map-123/events"
        assert json.loads(request.content) == {
            "type": "update_metadata", "data": {"asset_id": "asset-123", "metadata": expected},
            "user_session_id": "viewer-123",
        }
        return httpx.Response(201, json={
            "event_id": "event-123", "type": "update_metadata", "asset_id": "asset-123", "created_at": "now",
        })

    with httpx.Client(base_url="http://test", transport=httpx.MockTransport(handle)) as client:
        session = MapSession(client, "http://test", "map-123", "", "viewer-123", "")
        assert session.update_metadata("asset-123", metadata).asset_id == "asset-123"


def test_metadata_update_surfaces_server_error():
    transport = httpx.MockTransport(lambda request: httpx.Response(201, json={"error": "Asset not found"}))
    with httpx.Client(base_url="http://test", transport=transport) as client:
        session = MapSession(client, "http://test", "map-123", "", "viewer-123", "")
        with pytest.raises(ServerError, match="Asset not found"):
            session.update_metadata("missing", {"description": "new"})
