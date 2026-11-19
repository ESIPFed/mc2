"""Animated rasters: add_geotiff_frames (frame stacks) and add_video.

Validation, asset creation, what is persisted for session restore, and how
the files are served (video needs Range requests). Rendering itself is the
ordinary single-band path, covered by the geotiff tests.
"""

import json
import shutil
from unittest.mock import AsyncMock

import numpy as np
import pytest
import pytest_asyncio
import rasterio
from httpx import ASGITransport, AsyncClient
from PIL import Image
from rasterio.transform import from_origin

from mapcontrol_server.main import app
from mapcontrol_server.services import animation_service, geotiff_service, session_service
from mapcontrol_server.websocket import manager
from tests._harness import db_lifespan

# Minimal container headers: enough for the magic-byte sniff.
WEBM = b"\x1a\x45\xdf\xa3" + b"\x9f\x42\x86\x81\x01" + b"\x00" * 2000
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 2000


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
                yield client, map_id, socket, tmp_path
            finally:
                manager.disconnect(socket, map_id)


@pytest.fixture
def remote(tmp_path, monkeypatch):
    """Serve local files as https://example.test/<name>?<query> URLs."""
    served = tmp_path / "remote"
    served.mkdir()

    async def fake_download(url, max_bytes):
        name = url.split("://", 1)[1].split("/", 1)[1].split("?", 1)[0]
        src = served / name
        if not src.exists():
            raise FileNotFoundError(name)
        if src.stat().st_size > max_bytes:
            raise ValueError(f"File exceeds the {max_bytes / (1024 * 1024):.0f} MB limit")
        dst = tmp_path / f"dl-{name}"
        shutil.copyfile(src, dst)
        return str(dst), src.stat().st_size / (1024 * 1024)

    monkeypatch.setattr(geotiff_service, "_download_to_tempfile", fake_download)
    return served


def _tif(path, *, x0=-20.0, y0=10.0, res=1.0, w=20, h=10, value=0.5, crs="EPSG:4326"):
    data = np.full((1, h, w), value, dtype=np.float32)
    data[0, 2:5, 3:7] = 1.0
    with rasterio.open(
        path, "w", driver="GTiff", width=w, height=h, count=1, dtype="float32",
        crs=crs, transform=from_origin(x0, y0, res, res), nodata=-9999,
    ) as ds:
        ds.write(data)
    return str(path)


async def post(client, map_id, type_, data):
    r = await client.post(f"/api/maps/{map_id}/events", json={"type": type_, "data": data})
    assert r.status_code == 201, r.text
    return r.json()


def sent(socket, type_):
    return [
        json.loads(c.args[0])["data"]
        for c in socket.send_text.call_args_list
        if json.loads(c.args[0])["type"] == type_
    ]


async def asset_count(client, map_id):
    return len((await client.get(f"/api/maps/{map_id}/assets")).json())


# ─── Frame stacks: validation ────────────────────────────────────────────────

@pytest.mark.parametrize("data, needle", [
    ({"urls": ["a", "b"], "vmax": 1}, "vmin"),
    ({"urls": ["a", "b"], "vmin": 0}, "vmin"),
    ({"urls": ["a", "b"], "vmin": 1, "vmax": 1}, "greater"),
    ({"urls": ["a", "b"], "vmin": "x", "vmax": 1}, "finite"),
    ({"urls": ["a"], "vmin": 0, "vmax": 1}, "2..240"),
    ({"urls": [f"u{i}" for i in range(241)], "vmin": 0, "vmax": 1}, "2..240"),
    ({"urls": "a", "vmin": 0, "vmax": 1}, "urls"),
    ({"urls": ["a", "b"], "vmin": 0, "vmax": 1, "labels": ["x"]}, "labels"),
    ({"urls": ["a", "b"], "vmin": 0, "vmax": 1, "labels": ["x", "y" * 81]}, "max 80"),
    ({"urls": ["a", "b"], "vmin": 0, "vmax": 1, "fps": "fast"}, "fps"),
])
async def test_frames_rejects_bad_requests(ctx, data, needle):
    client, map_id, socket, _ = ctx
    out = await post(client, map_id, "add_geotiff_frames", data)
    assert out["error"] and needle in out["error"], out
    assert out["asset_id"] is None
    assert await asset_count(client, map_id) == 0
    assert sent(socket, "add_geotiff_frames") == []


def test_fps_is_clamped():
    base = {"urls": ["a", "b"], "vmin": 0, "vmax": 1}
    assert animation_service.validate_frames({**base, "fps": 100}).fps == 30
    assert animation_service.validate_frames({**base, "fps": 0.01}).fps == 0.25
    assert animation_service.validate_frames(base).fps == 4


async def test_mismatched_frame_grid_is_rejected_without_leftovers(ctx):
    client, map_id, socket, tmp = ctx
    urls = [
        _tif(tmp / "f0.tif"),
        _tif(tmp / "f1.tif"),
        _tif(tmp / "f2.tif", x0=-19.0),  # shifted one pixel east
    ]
    out = await post(client, map_id, "add_geotiff_frames", {"urls": urls, "vmin": 0, "vmax": 1})
    assert out["error"].startswith("Frame 3 of 3 (urls[2]) does not match frame 1"), out
    assert await asset_count(client, map_id) == 0
    assert sent(socket, "add_geotiff_frames") == []
    assert list((tmp / "files").glob("*.png")) == []  # no partial asset on disk


async def test_mismatched_pixel_size_is_rejected(ctx):
    client, map_id, _, tmp = ctx
    urls = [_tif(tmp / "f0.tif"), _tif(tmp / "f1.tif", res=0.5, w=40, h=20)]
    out = await post(client, map_id, "add_geotiff_frames", {"urls": urls, "vmin": 0, "vmax": 1})
    assert out["error"] and "Frame 2 of 2 (urls[1])" in out["error"], out


async def test_failing_frame_is_named_and_cleaned_up(ctx):
    client, map_id, _, tmp = ctx
    urls = [_tif(tmp / "f0.tif"), str(tmp / "missing.tif")]
    out = await post(client, map_id, "add_geotiff_frames", {"urls": urls, "vmin": 0, "vmax": 1})
    assert out["error"].startswith("Frame 2 of 2 (urls[1]):"), out
    assert list((tmp / "files").glob("*.png")) == []


async def test_frame_over_max_size_is_rejected(ctx, monkeypatch):
    client, map_id, _, tmp = ctx
    urls = [_tif(tmp / "f0.tif"), _tif(tmp / "f1.tif", w=400, h=400)]
    monkeypatch.setattr(geotiff_service, "_get_max_file_size_bytes", lambda config=None: 50_000)
    out = await post(client, map_id, "add_geotiff_frames", {"urls": urls, "vmin": 0, "vmax": 1})
    assert out["error"].startswith("Frame 2 of 2 (urls[1]):") and "limit" in out["error"], out


# ─── Frame stacks: creation, broadcast, persistence ─────────────────────────

async def test_frames_create_asset_broadcast_and_restore(ctx, remote):
    client, map_id, socket, tmp = ctx
    for i in range(3):
        _tif(remote / f"sst-{i}.tif", value=0.2 * i)
    urls = [f"https://example.test/sst-{i}.tif?legend=sst_anom&sig=abc" for i in range(3)]
    labels = ["2023-06-05", "2023-06-12", "2023-06-19"]
    out = await post(client, map_id, "add_geotiff_frames", {
        "urls": urls, "vmin": -3, "vmax": 3, "colormap": "RdBu_r", "fps": 6,
        "labels": labels, "loop": False, "name": "SST anomaly", "alpha": 0.8,
    })
    assert not out["error"], out
    aid = out["asset_id"]

    files = tmp / "files"
    for i in range(3):
        assert (files / f"{aid}-{i}.png").exists()
    # Frame 0 doubles as the asset's still (thumbnails, MCP raster resource).
    assert (files / f"{aid}.png").read_bytes() == (files / f"{aid}-0.png").read_bytes()

    msg = sent(socket, "add_geotiff_frames")[-1]
    assert msg["asset_id"] == aid
    assert msg["frame_urls"] == [f"/api/files/{aid}-{i}.png" for i in range(3)]
    assert msg["fps"] == 6 and msg["labels"] == labels
    assert msg["loop"] is False and msg["autoplay"] is True
    assert msg["style"] == {"opacity": 0.8} and msg["visible"] is True
    assert msg["name"] == "SST anomaly"
    assert msg["layer_z"] == {aid: msg["z_index"]}
    assert msg["bounds"] == pytest.approx([-20, 0, 0, 10], abs=0.1)  # warped to Web Mercator

    asset = (await client.get(f"/api/maps/{map_id}/assets/{aid}")).json()
    assert asset["asset_type"] == "geotiff_frames"
    assert asset["source_url"] == urls[0]  # query string preserved (legend id)
    assert asset["style"]["opacity"] == 0.8
    anim = asset["animation"]
    assert anim["kind"] == "frames" and anim["frame_count"] == 3
    assert anim["frame_urls"] == msg["frame_urls"]
    assert anim["fps"] == 6 and anim["labels"] == labels and anim["loop"] is False
    assert anim["autoplay"] is True
    assert anim["bounds"] == pytest.approx(msg["bounds"])

    # The summary listing (layer-manager polling) and the session snapshot
    # carry the same playback description.
    summary = (await client.get(f"/api/maps/{map_id}/assets?include_geojson=false")).json()
    assert summary[0]["animation"] == anim
    snap = await session_service.get_session_snapshot(map_id, "")
    assert snap.assets[0].animation == anim

    # Frames are served as PNGs.
    r = await client.get(msg["frame_urls"][1])
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"


async def test_frames_stack_below_vectors(ctx, tmp_path):
    client, map_id, _, tmp = ctx
    poly = '{"type":"Polygon","coordinates":[[[0,0],[1,0],[1,1],[0,0]]]}'
    vec = (await post(client, map_id, "add_polygon", {"geojson": poly}))["asset_id"]
    urls = [_tif(tmp / "f0.tif"), _tif(tmp / "f1.tif")]
    aid = (await post(client, map_id, "add_geotiff_frames", {"urls": urls, "vmin": 0, "vmax": 1}))["asset_id"]
    order = [a["asset_id"] for a in (await client.get(f"/api/maps/{map_id}/assets")).json()]
    assert order == [aid, vec]


async def test_antimeridian_3832_frames_match_singleband(ctx):
    """A frame renders exactly like add_geotiff_singleband (same pipeline)."""
    client, map_id, socket, tmp = ctx
    # EPSG:3832 (PDC Mercator, central meridian 150°E): 140°E..110°W.
    kw = dict(x0=-1_113_195.0, y0=1_000_000.0, res=50_000.0, w=200, h=40, crs="EPSG:3832")
    urls = [_tif(tmp / "p0.tif", **kw), _tif(tmp / "p1.tif", **kw)]
    single = await post(client, map_id, "add_geotiff_singleband", {"url": urls[0], "vmin": 0, "vmax": 1})
    frames = await post(client, map_id, "add_geotiff_frames", {"urls": urls, "vmin": 0, "vmax": 1})
    assert not frames["error"], frames
    s = sent(socket, "add_geotiff_singleband")[-1]
    f = sent(socket, "add_geotiff_frames")[-1]
    assert f["bounds"] == pytest.approx(s["bounds"])
    assert (f["width"], f["height"]) == (s["width"], s["height"])
    files = tmp / "files"
    assert np.array_equal(
        np.asarray(Image.open(files / f"{single['asset_id']}.png")),
        np.asarray(Image.open(files / f"{frames['asset_id']}-0.png")),
    )


# ─── Video: validation ──────────────────────────────────────────────────────

@pytest.mark.parametrize("bounds, ok", [
    ([140, -10, 250, 10], True),     # crosses the antimeridian: east > 180
    ([-180, -85.05, 180, 85.05], True),
    ([-170, -10, 190, 10], True),    # exactly west + 360
    ([180, -10, 200, 10], False),    # west must be < 180
    ([-181, -10, 0, 10], False),
    ([10, -10, 10, 10], False),      # east must be > west
    ([10, -10, 5, 10], False),
    ([-170, -10, 191, 10], False),   # more than 360 wide
    ([0, -86, 10, 10], False),
    ([0, 10, 10, 10], False),        # south < north
    ([0, 0, 10], False),
    ([0, 0, "x", 10], False),
])
def test_video_bounds_validation(bounds, ok):
    data = {"urls": ["https://example.test/v.webm"], "bounds": bounds}
    if ok:
        assert animation_service.validate_video(data).bounds == [float(b) for b in bounds]
    else:
        with pytest.raises(animation_service.AnimationError):
            animation_service.validate_video(data)


@pytest.mark.parametrize("urls", [[], ["a", "b", "c", "d"], "a", [1]])
def test_video_url_count(urls):
    with pytest.raises(animation_service.AnimationError):
        animation_service.validate_video({"urls": urls, "bounds": [0, 0, 1, 1]})


def test_sniff_video():
    assert animation_service.sniff_video(WEBM[:16]) == "webm"
    assert animation_service.sniff_video(MP4[:16]) == "mp4"
    assert animation_service.sniff_video(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8) is None
    assert animation_service.sniff_video(b"GIF89a") is None


def test_video_size_default_is_100mb():
    from mapcontrol_server.config import load_config
    assert load_config().video.max_file_size_mb == 100


async def test_video_rejects_wrong_magic_bytes(ctx, remote):
    client, map_id, socket, tmp = ctx
    (remote / "good.webm").write_bytes(WEBM)
    (remote / "fake.mp4").write_bytes(b"<html>not a video</html>" * 10)
    out = await post(client, map_id, "add_video", {
        "urls": ["https://example.test/good.webm", "https://example.test/fake.mp4"],
        "bounds": [0, 0, 10, 10],
    })
    assert out["error"].startswith("Video 2 of 2 (urls[1]) is not a WebM/Matroska or MP4"), out
    assert "example.test" not in out["error"]  # never echo (possibly signed) URLs
    assert await asset_count(client, map_id) == 0
    assert list((tmp / "files").glob("*")) == []  # the good one was removed again
    assert sent(socket, "add_video") == []


async def test_video_over_max_size_is_rejected(ctx, remote, monkeypatch):
    client, map_id, _, tmp = ctx
    (remote / "big.webm").write_bytes(WEBM + b"\x00" * 5000)
    monkeypatch.setattr(animation_service, "_max_video_bytes", lambda config: 4000)
    out = await post(client, map_id, "add_video", {
        "urls": ["https://example.test/big.webm"], "bounds": [0, 0, 10, 10],
    })
    assert out["error"].startswith("Video 1 of 1 (urls[0]):") and "limit" in out["error"], out
    assert await asset_count(client, map_id) == 0


async def test_video_bad_poster_removes_videos(ctx, remote):
    client, map_id, _, tmp = ctx
    (remote / "v.webm").write_bytes(WEBM)
    (remote / "poster.png").write_bytes(b"not an image")
    out = await post(client, map_id, "add_video", {
        "urls": ["https://example.test/v.webm"], "bounds": [0, 0, 10, 10],
        "poster_url": "https://example.test/poster.png",
    })
    assert "Poster" in out["error"], out
    assert list((tmp / "files").glob("*")) == []


# ─── Video: creation, persistence, serving ──────────────────────────────────

async def test_video_create_restore_and_range_serving(ctx, remote):
    client, map_id, socket, tmp = ctx
    (remote / "clip.webm").write_bytes(WEBM)
    (remote / "clip.mp4").write_bytes(MP4)
    Image.new("RGB", (8, 4), (200, 30, 30)).save(remote / "poster.jpg", "JPEG")
    urls = ["https://example.test/clip.webm?legend=sst_anom", "https://example.test/clip.mp4"]
    out = await post(client, map_id, "add_video", {
        "urls": urls, "bounds": [140, -20, 250, 20], "name": "El Niño",
        "poster_url": "https://example.test/poster.jpg", "autoplay": False, "alpha": 0.6,
    })
    assert not out["error"], out
    aid = out["asset_id"]
    files = tmp / "files"
    assert (files / f"{aid}-0.webm").read_bytes() == WEBM
    assert (files / f"{aid}-1.mp4").read_bytes() == MP4
    assert Image.open(files / f"{aid}.png").format == "PNG"  # JPEG poster stored as PNG

    msg = sent(socket, "add_video")[-1]
    assert msg["video_urls"] == [f"/api/files/{aid}-0.webm", f"/api/files/{aid}-1.mp4"]
    assert msg["poster_url"] == f"/api/files/{aid}.png"
    assert msg["bounds"] == [140, -20, 250, 20]
    assert msg["loop"] is True and msg["autoplay"] is False
    assert msg["style"] == {"opacity": 0.6} and msg["layer_z"] == {aid: msg["z_index"]}

    asset = (await client.get(f"/api/maps/{map_id}/assets/{aid}")).json()
    assert asset["asset_type"] == "geotiff_video"
    assert asset["source_url"] == urls[0]
    # East stays > 180: the overlay is one contiguous box across the antimeridian.
    assert asset["bbox"] == [140, -20, 250, 20]
    ring = json.loads(asset["geojson"])["geometry"]["coordinates"][0]
    assert [250, -20] in ring and [250, 20] in ring
    assert asset["animation"] == {
        "kind": "video", "video_urls": msg["video_urls"], "poster_url": msg["poster_url"],
        "loop": True, "autoplay": False, "bounds": [140, -20, 250, 20],
    }
    snap = await session_service.get_session_snapshot(map_id, "")
    assert snap.assets[0].animation == asset["animation"]

    # zoom_to_assets keeps the antimeridian-crossing box (not -180..180).
    await post(client, map_id, "zoom_to_assets", {"asset_ids": [aid]})
    vp = await session_service.get_effective_viewport(map_id, "")
    assert vp.bbox == [140, -20, 250, 20]

    # Served with the right type and HTTP Range support (Safari needs 206).
    r = await client.get(msg["video_urls"][0])
    assert r.status_code == 200 and r.headers["content-type"] == "video/webm"
    assert r.headers.get("accept-ranges") == "bytes"
    r = await client.get(msg["video_urls"][1], headers={"Range": "bytes=0-99"})
    assert r.status_code == 206 and r.headers["content-type"] == "video/mp4"
    assert r.headers["content-range"] == f"bytes 0-99/{len(MP4)}"
    assert r.content == MP4[:100]


async def test_video_without_poster(ctx, remote):
    client, map_id, _, tmp = ctx
    (remote / "clip.webm").write_bytes(WEBM)
    out = await post(client, map_id, "add_video", {
        "urls": ["https://example.test/clip.webm"], "bounds": [0, 0, 10, 10],
    })
    assert not out["error"], out
    asset = (await client.get(f"/api/maps/{map_id}/assets/{out['asset_id']}")).json()
    assert asset["animation"]["poster_url"] is None
    assert not (tmp / "files" / f"{out['asset_id']}.png").exists()


async def test_delete_animation_asset(ctx, tmp_path):
    client, map_id, socket, tmp = ctx
    urls = [_tif(tmp / "f0.tif"), _tif(tmp / "f1.tif")]
    aid = (await post(client, map_id, "add_geotiff_frames", {"urls": urls, "vmin": 0, "vmax": 1}))["asset_id"]
    await post(client, map_id, "delete_asset", {"asset_id": aid})
    assert await asset_count(client, map_id) == 0
    assert sent(socket, "delete_asset")[-1]["layer_z"] == {}


# ─── MCP tool ───────────────────────────────────────────────────────────────

async def test_mcp_add_animation(ctx, remote):
    from mapcontrol_server import mcp_tools
    client, map_id, _, tmp = ctx
    urls = [_tif(tmp / "f0.tif"), _tif(tmp / "f1.tif")]
    res = await mcp_tools.add_animation(map_id, "frames", urls, vmin=0, vmax=1, fps=2, name="t")
    aid = res.structuredContent["asset_id"]
    asset = (await client.get(f"/api/maps/{map_id}/assets/{aid}")).json()
    assert asset["asset_type"] == "geotiff_frames" and asset["animation"]["fps"] == 2
    with pytest.raises(ValueError, match="vmin"):
        await mcp_tools.add_animation(map_id, "frames", urls)
    with pytest.raises(ValueError, match="bounds"):
        await mcp_tools.add_animation(map_id, "video", ["https://example.test/v.webm"])
    (remote / "v.webm").write_bytes(WEBM)
    res = await mcp_tools.add_animation(map_id, "video", ["https://example.test/v.webm"], bounds=[0, 0, 10, 10])
    assert res.structuredContent["kind"] == "video" and res.structuredContent["asset_id"]
