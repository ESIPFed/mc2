"""Animated rasters: GeoTIFF frame stacks and pre-encoded video overlays.

Both are deliberately simple: a set of images (or one video) played on the
map with play/pause only. No timeline and no shared clock: play state is
per viewer and never stored.

- Frame stack (``add_geotiff_frames``): every frame goes through the normal
  single-band render path (same colormap, warp and masking as
  ``add_geotiff_singleband``) with ONE fixed vmin/vmax, so colours mean the
  same thing in every frame. All frames must land on the same grid.
- Video (``add_video``): 1..3 encodings of the same clip (e.g. VP9+alpha
  WebM, H.264 MP4 fallback), placed by four corners via MapLibre's native
  ``video`` source. Files are downloaded with a size cap and sniffed by magic
  bytes; anything that isn't WebM/Matroska or MP4 is rejected.

Validation errors never echo input URLs (they can be signed): frames are
named by position instead.
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

import httpx
from PIL import Image

from . import geotiff_service
from .geotiff_service import GeoTIFFError, GeoTIFFResult

logger = logging.getLogger("mapcontrol.animation")

MIN_FRAMES = 2
MAX_FRAMES = 240
MIN_FPS = 0.25
MAX_FPS = 30.0
DEFAULT_FPS = 4.0
MAX_LABEL_CHARS = 80
# Frames rendered at once. Each render is CPU-bound in a worker thread and
# holds a full-resolution float64 copy of its band, so keep this small.
FRAME_CONCURRENCY = 4

MAX_VIDEO_URLS = 3
DEFAULT_MAX_VIDEO_MB = 100
# Web Mercator is undefined past this latitude.
MERCATOR_MAX_LAT = 85.05


class AnimationError(ValueError):
    """A request the caller must fix (bad parameters, mismatched frames...)."""


def _finite(value) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _file_dir(config) -> Path:
    return geotiff_service._get_file_dir(config)


def _cleanup(paths: list[Path]) -> None:
    for p in paths:
        try:
            p.unlink(missing_ok=True)
        except OSError:
            logger.warning("Could not remove %s", p)


# ─── Frame stacks ────────────────────────────────────────────────────────────


@dataclass
class FramesParams:
    urls: list[str]
    vmin: float
    vmax: float
    fps: float
    labels: list[str] | None
    loop: bool
    autoplay: bool
    band: int
    colormap: str
    nodata: float | None


def validate_frames(data: dict) -> FramesParams:
    """Validate an add_geotiff_frames payload. Raises AnimationError."""
    urls = data.get("urls")
    if not isinstance(urls, list) or not all(isinstance(u, str) and u for u in urls):
        raise AnimationError("add_geotiff_frames requires 'urls': a list of GeoTIFF URLs")
    if not MIN_FRAMES <= len(urls) <= MAX_FRAMES:
        raise AnimationError(
            f"add_geotiff_frames takes {MIN_FRAMES}..{MAX_FRAMES} frames, got {len(urls)}"
        )

    if data.get("vmin") is None or data.get("vmax") is None:
        raise AnimationError(
            "add_geotiff_frames requires a fixed 'vmin' and 'vmax' (per-frame "
            "automatic ranges would make colours jump between frames)"
        )
    vmin, vmax = _finite(data.get("vmin")), _finite(data.get("vmax"))
    if vmin is None or vmax is None:
        raise AnimationError("'vmin' and 'vmax' must be finite numbers")
    if not vmax > vmin:
        raise AnimationError("'vmax' must be greater than 'vmin'")

    fps = _finite(data.get("fps", DEFAULT_FPS)) if data.get("fps") is not None else DEFAULT_FPS
    if fps is None:
        raise AnimationError("'fps' must be a finite number")
    fps = min(MAX_FPS, max(MIN_FPS, fps))

    labels = data.get("labels")
    if labels is not None:
        if not isinstance(labels, list) or not all(isinstance(l, str) for l in labels):
            raise AnimationError("'labels' must be a list of strings")
        if len(labels) != len(urls):
            raise AnimationError(
                f"'labels' has {len(labels)} entries but there are {len(urls)} frames"
            )
        for i, label in enumerate(labels):
            if len(label) > MAX_LABEL_CHARS:
                raise AnimationError(
                    f"labels[{i}] is {len(label)} characters (max {MAX_LABEL_CHARS})"
                )

    band = data.get("band", 1)
    if isinstance(band, bool) or not isinstance(band, int):
        raise AnimationError("'band' must be an integer (1-based)")

    nodata = data.get("nodata")
    if nodata is not None:
        nodata = _finite(nodata)
        if nodata is None:
            raise AnimationError("'nodata' must be a finite number")

    colormap = data.get("colormap") or "viridis"
    if not isinstance(colormap, str):
        raise AnimationError("'colormap' must be a matplotlib colormap name")

    return FramesParams(
        urls=urls, vmin=vmin, vmax=vmax, fps=fps, labels=labels,
        loop=data.get("loop", True) is not False,
        autoplay=data.get("autoplay", True) is not False,
        band=band, colormap=colormap, nodata=nodata,
    )


def _same_grid(a: GeoTIFFResult, b: GeoTIFFResult) -> bool:
    if (a.width, a.height) != (b.width, b.height):
        return False
    # Sub-pixel tolerance: identical grids reproject to identical bounds up to
    # float noise; anything a hundredth of a pixel off is a different grid.
    px = min((a.bounds[2] - a.bounds[0]) / a.width, (a.bounds[3] - a.bounds[1]) / a.height)
    tol = max(1e-9, 0.01 * abs(px))
    return all(abs(x - y) <= tol for x, y in zip(a.bounds, b.bounds))


async def render_frames(
    params: FramesParams, asset_id: str, config
) -> tuple[list[GeoTIFFResult], list[str]]:
    """Render every frame to ``{asset_id}-{i}.png`` (+ frame 0 as
    ``{asset_id}.png``). Returns (results, served frame URLs).

    Raises AnimationError (and removes every file it wrote) when any frame
    fails or lands on a different grid than frame 0.
    """
    file_dir = _file_dir(config)
    sem = asyncio.Semaphore(FRAME_CONCURRENCY)
    failed = asyncio.Event()
    n = len(params.urls)

    async def one(i: int, url: str):
        async with sem:
            if failed.is_set():
                return None
            result = await geotiff_service.process_geotiff_singleband(
                source=url,
                asset_id=f"{asset_id}-{i}",
                band=params.band,
                colormap=params.colormap,
                vmin=params.vmin,
                vmax=params.vmax,
                nodata=params.nodata,
                config=config,
            )
            if isinstance(result, GeoTIFFError):
                failed.set()
            return result

    written = [file_dir / f"{asset_id}-{i}.png" for i in range(n)]
    results = await asyncio.gather(*(one(i, u) for i, u in enumerate(params.urls)))

    error: str | None = None
    for i, result in enumerate(results):
        if isinstance(result, GeoTIFFError):
            error = f"Frame {i + 1} of {n} (urls[{i}]): {result.detail}"
            break
    if error is None and any(r is None for r in results):
        error = "Frame rendering was aborted"  # defensive: only after a failure
    if error is None:
        first = results[0]
        for i, result in enumerate(results[1:], start=1):
            if not _same_grid(first, result):
                error = (
                    f"Frame {i + 1} of {n} (urls[{i}]) does not match frame 1: "
                    f"bounds {[round(v, 6) for v in result.bounds]} at "
                    f"{result.width}x{result.height} px vs "
                    f"{[round(v, 6) for v in first.bounds]} at "
                    f"{first.width}x{first.height} px. Every frame must share "
                    "one grid (same CRS, extent and pixel size)."
                )
                break
    if error is not None:
        _cleanup(written)
        raise AnimationError(error)

    # Frame 0 doubles as the asset's still image: thumbnails, the MCP raster
    # resource and anything else that reads /api/files/{asset_id}.png.
    shutil.copyfile(written[0], file_dir / f"{asset_id}.png")
    return list(results), [r.image_url for r in results]


# ─── Video overlays ──────────────────────────────────────────────────────────


@dataclass
class VideoParams:
    urls: list[str]
    bounds: list[float]
    poster_url: str | None
    loop: bool
    autoplay: bool


def validate_video(data: dict) -> VideoParams:
    """Validate an add_video payload. Raises AnimationError."""
    urls = data.get("urls")
    if not isinstance(urls, list) or not all(isinstance(u, str) and u for u in urls):
        raise AnimationError("add_video requires 'urls': a list of video URLs")
    if not 1 <= len(urls) <= MAX_VIDEO_URLS:
        raise AnimationError(f"add_video takes 1..{MAX_VIDEO_URLS} urls, got {len(urls)}")

    bounds = data.get("bounds")
    if not isinstance(bounds, (list, tuple)) or len(bounds) != 4:
        raise AnimationError("add_video requires 'bounds': [west, south, east, north] in EPSG:4326")
    vals = [_finite(v) for v in bounds]
    if any(v is None for v in vals):
        raise AnimationError("'bounds' must be four finite numbers")
    west, south, east, north = vals
    # East may pass 180 for an overlay crossing the antimeridian (MapLibre
    # draws corner longitudes > 180 on the next world copy); never fold it
    # back below west.
    if not -180.0 <= west < 180.0:
        raise AnimationError("bounds west must be in [-180, 180)")
    if not west < east <= west + 360.0:
        raise AnimationError(
            "bounds east must be greater than west and at most west + 360 "
            "(use east > 180 for an overlay crossing the antimeridian)"
        )
    if not -MERCATOR_MAX_LAT <= south < north <= MERCATOR_MAX_LAT:
        raise AnimationError(
            f"bounds must satisfy -{MERCATOR_MAX_LAT} <= south < north <= {MERCATOR_MAX_LAT}"
        )

    poster = data.get("poster_url")
    if poster is not None and (not isinstance(poster, str) or not poster):
        raise AnimationError("'poster_url' must be a URL string")

    return VideoParams(
        urls=urls, bounds=[west, south, east, north], poster_url=poster,
        loop=data.get("loop", True) is not False,
        autoplay=data.get("autoplay", True) is not False,
    )


def sniff_video(head: bytes) -> str | None:
    """'webm' for WebM/Matroska (EBML), 'mp4' for ISO-BMFF (ftyp), else None."""
    if head[:4] == b"\x1a\x45\xdf\xa3":
        return "webm"
    if len(head) >= 8 and head[4:8] == b"ftyp":
        return "mp4"
    return None


def _max_video_bytes(config) -> int:
    mb = DEFAULT_MAX_VIDEO_MB
    if config is not None and hasattr(config, "video"):
        mb = getattr(config.video, "max_file_size_mb", DEFAULT_MAX_VIDEO_MB)
    return int(mb * 1024 * 1024)


async def _fetch_local_or_remote(source: str, max_bytes: int, what: str) -> tuple[str, bool]:
    """(path, is_temp) for a URL / file:// URI / local path, size-capped."""
    local = geotiff_service._resolve_source(source)
    if local is not None:
        if not os.path.isfile(local):
            raise AnimationError(f"{what}: file not found")
        if os.path.getsize(local) > max_bytes:
            raise AnimationError(
                f"{what} is {os.path.getsize(local) / (1024 * 1024):.1f} MB, exceeding "
                f"the {max_bytes / (1024 * 1024):.0f} MB limit"
            )
        return local, False
    try:
        path, _ = await geotiff_service._download_to_tempfile(source, max_bytes)
    except ValueError as exc:  # size limit
        raise AnimationError(f"{what}: {exc}") from None
    except httpx.HTTPStatusError as exc:
        raise AnimationError(f"{what}: download failed (HTTP {exc.response.status_code})") from None
    except Exception as exc:
        raise AnimationError(f"{what}: download failed ({type(exc).__name__})") from None
    return path, True


async def store_video(
    params: VideoParams, asset_id: str, config
) -> tuple[list[str], str | None]:
    """Download, sniff and store each encoding as ``{asset_id}-{i}.webm|.mp4``
    and the optional poster as ``{asset_id}.png``.

    Returns (served video URLs, served poster URL or None). Raises
    AnimationError and removes everything it wrote on any failure.
    """
    file_dir = _file_dir(config)
    max_bytes = _max_video_bytes(config)
    written: list[Path] = []
    served: list[str] = []
    n = len(params.urls)
    try:
        for i, url in enumerate(params.urls):
            what = f"Video {i + 1} of {n} (urls[{i}])"
            path, is_temp = await _fetch_local_or_remote(url, max_bytes, what)
            try:
                with open(path, "rb") as f:
                    kind = sniff_video(f.read(16))
                if kind is None:
                    raise AnimationError(
                        f"{what} is not a WebM/Matroska or MP4 file (checked the "
                        "file's magic bytes; the URL extension is ignored)"
                    )
                dest = file_dir / f"{asset_id}-{i}.{kind}"
                written.append(dest)
                await asyncio.to_thread(shutil.copyfile, path, dest)
                served.append(f"/api/files/{dest.name}")
            finally:
                if is_temp and os.path.exists(path):
                    os.unlink(path)

        poster_served = None
        if params.poster_url:
            # A poster is a still image: the image limit, not the video one.
            path, is_temp = await _fetch_local_or_remote(
                params.poster_url, geotiff_service._get_max_file_size_bytes(config), "Poster"
            )
            try:
                dest = file_dir / f"{asset_id}.png"
                written.append(dest)

                def _to_png():
                    try:
                        with Image.open(path) as img:
                            if img.format not in ("PNG", "JPEG"):
                                raise AnimationError("Poster must be a PNG or JPEG image")
                            img.convert("RGBA").save(dest, "PNG")
                    except AnimationError:
                        raise
                    except Exception:
                        raise AnimationError("Poster is not a readable PNG or JPEG image") from None

                await asyncio.to_thread(_to_png)
                poster_served = f"/api/files/{dest.name}"
            finally:
                if is_temp and os.path.exists(path):
                    os.unlink(path)
    except BaseException:
        _cleanup(written)
        raise
    return served, poster_served
