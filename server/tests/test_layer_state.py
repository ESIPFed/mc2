"""Stored state must be what the map draws.

Each test pins one place where the DB, the REST API, live broadcasts and
session restore used to disagree about the same asset, which left every
layer manager (MC2's own panel, embedding apps) showing the wrong thing:

  - update_style / PATCH replaced the whole stored style while viewers merged;
  - set_opacity was broadcast-only (lost on reload, absent from GET);
  - GeoTIFF alpha and tile-layer opacity were never stored;
  - vector defaults were viewer-only (GET said null, the map drew blue);
  - add_* broadcasts carried the raw request style (status unexpanded);
  - the session snapshot dropped z_index, and broadcasts carried no z map,
    so viewers kept their own numbering after a raster insert or reorder.
"""

import json
from unittest.mock import AsyncMock

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from mapcontrol_server.config import load_config
from mapcontrol_server.main import app
from mapcontrol_server.services import session_service
from mapcontrol_server.websocket import manager
from tests._harness import db_lifespan

POLY = '{"type":"Feature","geometry":{"type":"Polygon","coordinates":[[[-97.7,30.2],[-97.7,30.3],[-97.6,30.3],[-97.6,30.2],[-97.7,30.2]]]},"properties":{}}'
POINT = '{"type":"Point","coordinates":[-97.7,30.2]}'
TILE = "https://tile.example/{z}/{x}/{y}.png"


@pytest_asyncio.fixture
async def ctx(tmp_path, monkeypatch):
    monkeypatch.setenv("MAPCONTROL_DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("MAPCONTROL_FILE_DIR", str(tmp_path / "files"))
    async with db_lifespan():
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            map_id = (await client.post("/api/maps")).json()["map_id"]
            socket = AsyncMock()
            await manager.connect(socket, map_id, "viewer")
            try:
                yield client, map_id, socket
            finally:
                manager.disconnect(socket, map_id)


async def event(client, map_id, type_, data):
    r = await client.post(f"/api/maps/{map_id}/events", json={"type": type_, "data": data})
    assert r.status_code == 201, r.text
    body = r.json()
    assert not body.get("error"), body
    return body


async def stored(client, map_id, asset_id):
    r = await client.get(f"/api/maps/{map_id}/assets/{asset_id}")
    assert r.status_code == 200
    return r.json()


def sent(socket, type_):
    """Broadcast payloads of one type, in order."""
    out = []
    for call in socket.send_text.call_args_list:
        msg = json.loads(call.args[0])
        if msg["type"] == type_:
            out.append(msg["data"])
    return out


# ─── Style: merge, defaults, broadcasts ─────────────────────────────────────

async def test_vector_defaults_are_stored(ctx):
    client, map_id, _ = ctx
    aid = (await event(client, map_id, "add_polygon", {"geojson": POLY}))["asset_id"]
    style = (await stored(client, map_id, aid))["style"]
    defaults = load_config().map.style
    assert style["fill_color"] == defaults.fill_color
    assert style["stroke_color"] == defaults.stroke_color
    assert style["stroke_width"] == defaults.stroke_width


async def test_masked_polygon_keeps_transparent_interior(ctx):
    client, map_id, _ = ctx
    aid = (await event(client, map_id, "add_polygon", {"geojson": POLY, "style": {"mask": True}}))["asset_id"]
    style = (await stored(client, map_id, aid))["style"]
    assert style["fill_color"] is None  # the mask look: no fill
    assert style["stroke_color"]  # outline defaults still materialize


async def test_add_broadcast_carries_stored_style(ctx):
    client, map_id, socket = ctx
    await event(client, map_id, "add_point", {"geojson": POINT, "style": {"status": "muted"}})
    data = sent(socket, "add_point")[0]
    # Expanded server-side: the live map used to get only {"status": "muted"}
    # and draw nothing different until a reload.
    assert data["style"]["opacity"] == 0.35
    assert data["style"]["fill_color"] == "#9ca3af"
    assert data["visible"] is True


async def test_update_style_merges(ctx):
    client, map_id, socket = ctx
    aid = (await event(client, map_id, "add_polygon", {
        "geojson": POLY, "style": {"fill_color": "#ff000040", "stroke_color": "#00ff00", "label": True},
    }))["asset_id"]
    await event(client, map_id, "update_style", {"asset_id": aid, "style": {"opacity": 0.5}})
    style = (await stored(client, map_id, aid))["style"]
    assert style["fill_color"] == "#ff000040"
    assert style["stroke_color"] == "#00ff00"
    assert style["label"] is True
    assert style["opacity"] == 0.5
    # Viewers get the complete result, flagged as such.
    data = sent(socket, "update_style")[-1]
    assert data["full"] is True
    assert data["style"]["fill_color"] == "#ff000040" and data["style"]["opacity"] == 0.5


async def test_update_style_null_clears_a_key(ctx):
    client, map_id, _ = ctx
    aid = (await event(client, map_id, "add_polygon", {
        "geojson": POLY, "style": {"line_dash": [2, 2], "label": True},
    }))["asset_id"]
    await event(client, map_id, "update_style", {"asset_id": aid, "style": {"line_dash": None}})
    style = (await stored(client, map_id, aid))["style"]
    assert style["line_dash"] is None
    assert style["label"] is True


async def test_status_update_keeps_the_assets_own_colors(ctx):
    client, map_id, _ = ctx
    aid = (await event(client, map_id, "add_point", {
        "geojson": POINT, "style": {"fill_color": "#e11d48", "label": True},
    }))["asset_id"]
    await event(client, map_id, "update_style", {"asset_id": aid, "style": {"status": "done"}})
    style = (await stored(client, map_id, aid))["style"]
    assert style["fill_color"] == "#e11d48"  # used to revert to the default after reload
    assert style["label"] is True
    assert style["stroke_color"] == "#22c55e"  # the preset still applies
    assert style["animate"] == []


async def test_patch_style_merges_and_broadcasts_full_style(ctx):
    client, map_id, socket = ctx
    aid = (await event(client, map_id, "add_polygon", {
        "geojson": POLY, "style": {"fill_color": "#ff000040"},
    }))["asset_id"]
    r = await client.patch(f"/api/maps/{map_id}/assets/{aid}", json={"style": {"stroke_width": 5}})
    assert r.status_code == 200
    assert r.json()["style"]["fill_color"] == "#ff000040"
    data = sent(socket, "asset_updated")[-1]
    assert "geojson" not in data
    assert data["full"] is True
    assert data["style"]["fill_color"] == "#ff000040" and data["style"]["stroke_width"] == 5


async def test_update_style_missing_asset_errors(ctx):
    client, map_id, _ = ctx
    r = await client.post(f"/api/maps/{map_id}/events", json={
        "type": "update_style", "data": {"asset_id": "nope", "style": {"opacity": 1}},
    })
    assert r.json()["error"] == "Asset not found"


# ─── Opacity ────────────────────────────────────────────────────────────────

async def test_set_opacity_persists(ctx):
    client, map_id, socket = ctx
    aid = (await event(client, map_id, "add_polygon", {
        "geojson": POLY, "style": {"fill_color": "#ff000040"},
    }))["asset_id"]
    await event(client, map_id, "set_opacity", {"asset_id": aid, "opacity": 0.3})
    style = (await stored(client, map_id, aid))["style"]
    assert style["opacity"] == 0.3
    assert style["fill_color"] == "#ff000040"
    data = sent(socket, "set_opacity")[-1]
    assert data["opacity"] == 0.3 and data["style"]["opacity"] == 0.3 and data["full"] is True


async def test_set_opacity_accepts_percentages(ctx):
    client, map_id, _ = ctx
    aid = (await event(client, map_id, "add_polygon", {"geojson": POLY}))["asset_id"]
    await event(client, map_id, "set_opacity", {"asset_id": aid, "opacity": 40})
    assert (await stored(client, map_id, aid))["style"]["opacity"] == 0.4


async def test_set_opacity_rejects_garbage(ctx):
    client, map_id, _ = ctx
    aid = (await event(client, map_id, "add_polygon", {"geojson": POLY}))["asset_id"]
    r = await client.post(f"/api/maps/{map_id}/events", json={
        "type": "set_opacity", "data": {"asset_id": aid, "opacity": "high"},
    })
    assert "opacity" in r.json()["error"]


async def test_tile_layer_opacity_and_visibility_are_stored(ctx):
    client, map_id, socket = ctx
    aid = (await event(client, map_id, "add_tile_layer", {
        "url": TILE, "name": "Tiles", "opacity": 60, "visible": False,
    }))["asset_id"]
    asset = await stored(client, map_id, aid)
    assert asset["style"]["opacity"] == 0.6
    assert asset["visible"] is False
    assert asset["source_url"] == TILE  # what session restore re-adds it from
    data = sent(socket, "add_tile_layer")[-1]
    assert data["opacity"] == 0.6 and data["visible"] is False


# ─── Stacking ───────────────────────────────────────────────────────────────

async def test_raster_insert_broadcasts_the_shifted_z_map(ctx):
    client, map_id, socket = ctx
    v1 = (await event(client, map_id, "add_polygon", {"geojson": POLY, "name": "v1"}))["asset_id"]
    v2 = (await event(client, map_id, "add_polygon", {"geojson": POLY, "name": "v2"}))["asset_id"]
    r = (await event(client, map_id, "add_tile_layer", {"url": TILE, "name": "r"}))["asset_id"]
    layer_z = sent(socket, "add_tile_layer")[-1]["layer_z"]
    # The raster took the lowest vector's slot and both vectors moved up;
    # viewers used to keep the vectors' old numbers and stack the raster
    # above v1.
    assert layer_z[r] < layer_z[v1] < layer_z[v2]
    listed = [a["asset_id"] for a in (await client.get(f"/api/maps/{map_id}/assets")).json()]
    assert listed == [r, v1, v2]


async def test_reorder_renumbers_everything_and_keeps_unlisted_below(ctx):
    client, map_id, socket = ctx
    a = (await event(client, map_id, "add_polygon", {"geojson": POLY, "name": "a"}))["asset_id"]
    b = (await event(client, map_id, "add_polygon", {"geojson": POLY, "name": "b"}))["asset_id"]
    c = (await event(client, map_id, "add_polygon", {"geojson": POLY, "name": "c"}))["asset_id"]
    # Partial list, top-most first: a over b. c is unlisted → below them,
    # which is what the live map does with the same list.
    await event(client, map_id, "reorder_assets", {"asset_ids": [a, b]})
    listed = [x["asset_id"] for x in (await client.get(f"/api/maps/{map_id}/assets")).json()]
    assert listed == [c, b, a]
    layer_z = sent(socket, "reorder_assets")[-1]["layer_z"]
    assert sorted(layer_z.values()) == [0, 1, 2]
    assert layer_z[c] < layer_z[b] < layer_z[a]


async def test_move_layer_broadcasts_z_map(ctx):
    client, map_id, socket = ctx
    a = (await event(client, map_id, "add_polygon", {"geojson": POLY}))["asset_id"]
    b = (await event(client, map_id, "add_polygon", {"geojson": POLY}))["asset_id"]
    await event(client, map_id, "move_layer", {"asset_id": a, "position": "top"})
    layer_z = sent(socket, "move_layer")[-1]["layer_z"]
    assert layer_z[a] > layer_z[b]


async def test_snapshot_carries_z_index_and_style(ctx):
    client, map_id, _ = ctx
    v = (await event(client, map_id, "add_polygon", {"geojson": POLY}))["asset_id"]
    r = (await event(client, map_id, "add_tile_layer", {"url": TILE, "opacity": 0.5}))["asset_id"]
    await event(client, map_id, "set_opacity", {"asset_id": v, "opacity": 0.25})
    snap = await session_service.get_session_snapshot(map_id, "viewer")
    by_id = {a.asset_id: a for a in snap.assets}
    assert by_id[r].z_index < by_id[v].z_index  # used to be 0 for every asset
    assert by_id[v].style.opacity == 0.25
    assert by_id[r].style.opacity == 0.5
    assert by_id[v].bbox is not None
