"""Tests for the animate/opacity/status Style-schema extension.

Covers the AssetStyle model semantics (status preset expansion, explicit
fields winning over presets, glow back-compat) and the event round-trip
(add_point with status → stored expanded style; update_style broadcasts the
expanded style).
"""

import os
import tempfile

import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from mapcontrol_server.main import app, lifespan
from mapcontrol_server.models import AssetStyle

POINT_GEOJSON = '{"type":"Feature","geometry":{"type":"Point","coordinates":[-97.7,30.27]},"properties":{"name":"P1"}}'


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
async def map_with_point(client):
    map_resp = await client.post("/api/maps")
    map_id = map_resp.json()["map_id"]
    event_resp = await client.post(f"/api/maps/{map_id}/events", json={
        "type": "add_point",
        "data": {"geojson": POINT_GEOJSON, "name": "P1"},
    })
    return map_id, event_resp.json()["asset_id"]


# ─── Model semantics ─────────────────────────────────────────

def test_status_active_expands_to_pulse():
    s = AssetStyle(status="active")
    assert s.animate is not None and len(s.animate) == 3
    props = {e["property"] for e in s.animate}
    assert props == {"opacity", "circle_radius", "ripple"}


def test_status_active_ripple_expands_outward():
    s = AssetStyle(status="active")
    ripple = next(e for e in s.animate if e["property"] == "ripple")
    assert ripple["to"] > ripple["from"]  # halo grows outward
    assert ripple["period"] > 0


def test_custom_ripple_with_color_round_trips():
    fx = [{"property": "ripple", "from": 8, "to": 30, "period": 2.0,
           "color": "#38bdf8"}]
    s = AssetStyle(animate=fx)
    assert s.model_dump(exclude_none=True)["animate"] == fx


def test_status_done_stops_animation_and_marks_complete():
    s = AssetStyle(status="done")
    assert s.animate == []           # explicit stop
    assert s.opacity == 1.0
    assert s.stroke_color            # success stroke set


def test_status_muted_grays_out():
    s = AssetStyle(status="muted")
    assert s.animate == []
    assert s.opacity is not None and s.opacity < 0.5
    assert s.fill_color and s.stroke_color


def test_explicit_fields_win_over_status_preset():
    s = AssetStyle(status="muted", opacity=0.6, fill_color="#123456")
    assert s.opacity == 0.6
    assert s.fill_color == "#123456"


def test_status_preserved_for_readback():
    s = AssetStyle(status="active")
    assert s.status == "active"
    assert s.model_dump(exclude_none=True)["status"] == "active"


def test_glow_untouched_by_animate_machinery():
    s = AssetStyle(glow=True)
    assert s.glow is True
    assert s.animate is None  # glow compiles client-side; model leaves it


def test_custom_animate_round_trips():
    fx = [{"property": "stroke_width", "from": 1, "to": 5, "period": 0.8}]
    s = AssetStyle(animate=fx)
    assert s.model_dump(exclude_none=True)["animate"] == fx


# ─── Event round-trip ────────────────────────────────────────

async def test_add_point_with_status_stores_expanded_style(client):
    map_resp = await client.post("/api/maps")
    map_id = map_resp.json()["map_id"]
    event_resp = await client.post(f"/api/maps/{map_id}/events", json={
        "type": "add_point",
        "data": {"geojson": POINT_GEOJSON, "name": "P1",
                 "style": {"status": "active"}},
    })
    asset_id = event_resp.json()["asset_id"]
    asset = (await client.get(f"/api/maps/{map_id}/assets/{asset_id}")).json()
    style = asset["style"]
    assert style["status"] == "active"
    assert style["animate"] and len(style["animate"]) == 3


async def test_update_style_status_lifecycle(client, map_with_point):
    map_id, asset_id = map_with_point
    # active → pulse
    r = await client.post(f"/api/maps/{map_id}/events", json={
        "type": "update_style",
        "data": {"asset_id": asset_id, "style": {"status": "active"}},
    })
    assert r.status_code == 200 and not r.json().get("error")
    # done → stop + complete
    r = await client.post(f"/api/maps/{map_id}/events", json={
        "type": "update_style",
        "data": {"asset_id": asset_id, "style": {"status": "done"}},
    })
    assert r.status_code == 200 and not r.json().get("error")
    asset = (await client.get(f"/api/maps/{map_id}/assets/{asset_id}")).json()
    assert asset["style"]["status"] == "done"
    assert asset["style"]["animate"] == []
    assert asset["style"]["opacity"] == 1.0
    # muted → gray
    r = await client.post(f"/api/maps/{map_id}/events", json={
        "type": "update_style",
        "data": {"asset_id": asset_id, "style": {"status": "muted"}},
    })
    assert r.status_code == 200
    asset = (await client.get(f"/api/maps/{map_id}/assets/{asset_id}")).json()
    assert asset["style"]["opacity"] < 0.5


async def test_update_style_opacity_only(client, map_with_point):
    map_id, asset_id = map_with_point
    r = await client.post(f"/api/maps/{map_id}/events", json={
        "type": "update_style",
        "data": {"asset_id": asset_id, "style": {"opacity": 0.42}},
    })
    assert r.status_code == 200 and not r.json().get("error")
    asset = (await client.get(f"/api/maps/{map_id}/assets/{asset_id}")).json()
    assert asset["style"]["opacity"] == 0.42
