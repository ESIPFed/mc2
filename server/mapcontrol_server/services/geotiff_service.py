"""GeoTIFF processing service.

Handles downloading, validation, and rendering of GeoTIFF files
to georeferenced RGBA PNG images for display on the map.

Supports:
- RGB mode: 3-band composite with band selection
- Single-band mode: colormap rendering with percentile thresholding
- NaN/nodata handling (transparent pixels)
- Alpha/opacity control
- File size limits with friendly error messages
"""

from __future__ import annotations

import io
import logging
import os
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import rasterio
from rasterio.warp import transform_bounds
from matplotlib import colormaps
from PIL import Image

logger = logging.getLogger("mapcontrol.geotiff")

# ─── Configuration defaults ──────────────────────────────────────────────────

DEFAULT_MAX_FILE_SIZE_MB = 50
TILE_SERVER_MESSAGE = (
    "Tile server support for large rasters is coming soon — stay tuned!"
)

# ─── Result dataclass ────────────────────────────────────────────────────────


@dataclass
class GeoTIFFResult:
    """Result of processing a GeoTIFF file."""

    png_path: str  # Path to the rendered PNG file
    bounds: list[float]  # [minLon, minLat, maxLon, maxLat] in EPSG:4326
    width: int  # Pixel width of the output
    height: int  # Pixel height of the output
    crs: str  # Original CRS of the GeoTIFF
    band_count: int  # Number of bands in the source
    image_url: str  # Relative URL to serve the image


@dataclass
class GeoTIFFError:
    """Error result when GeoTIFF processing fails."""

    error: str
    detail: str
    file_size_mb: float | None = None


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _get_file_dir(config=None) -> Path:
    """Get the file storage directory, creating it if needed."""
    if config and hasattr(config, "storage"):
        file_dir = Path(config.storage.file_dir)
    else:
        file_dir = Path("./data/files")
    file_dir.mkdir(parents=True, exist_ok=True)
    return file_dir


def _get_max_file_size_bytes(config=None) -> int:
    """Get max file size in bytes from config."""
    max_mb = DEFAULT_MAX_FILE_SIZE_MB
    if config and hasattr(config, "geotiff"):
        max_mb = getattr(config.geotiff, "max_file_size_mb", DEFAULT_MAX_FILE_SIZE_MB)
    return max_mb * 1024 * 1024


def _get_tile_server_message(config=None) -> str:
    """Get the tile server coming-soon message."""
    if config and hasattr(config, "geotiff"):
        return getattr(config.geotiff, "tile_server_message", TILE_SERVER_MESSAGE)
    return TILE_SERVER_MESSAGE


async def _download_to_tempfile(url: str, max_bytes: int) -> tuple[str, float]:
    """Download a URL to a temp file. Returns (path, size_mb).
    Raises ValueError if too large.
    """
    async with httpx.AsyncClient(follow_redirects=True) as client:
        # Check Content-Length first if available
        head_resp = await client.head(url, timeout=10.0)
        content_length = head_resp.headers.get("content-length")
        if content_length and int(content_length) > max_bytes:
            size_mb = int(content_length) / (1024 * 1024)
            raise ValueError(
                f"File is {size_mb:.1f} MB, exceeding the "
                f"{max_bytes / (1024 * 1024):.0f} MB limit."
            )

        # Stream download
        tmp = tempfile.NamedTemporaryFile(suffix=".tif", delete=False)
        total = 0
        try:
            async with client.stream("GET", url, timeout=60.0) as resp:
                resp.raise_for_status()
                async for chunk in resp.aiter_bytes(chunk_size=65536):
                    total += len(chunk)
                    if total > max_bytes:
                        tmp.close()
                        os.unlink(tmp.name)
                        raise ValueError(
                            f"File exceeds the "
                            f"{max_bytes / (1024 * 1024):.0f} MB limit "
                            f"(downloaded {total / (1024 * 1024):.1f} MB so far)."
                        )
                    tmp.write(chunk)
            tmp.close()
        except Exception:
            tmp.close()
            if os.path.exists(tmp.name):
                os.unlink(tmp.name)
            raise

    return tmp.name, total / (1024 * 1024)


def _resolve_source(source: str) -> str:
    """Resolve a source to a local file path.
    Supports file:// URIs and plain paths. Returns the path string.
    For http/https, returns None (caller should download).
    """
    if source.startswith("file://"):
        return source[7:]
    if source.startswith(("http://", "https://")):
        return None  # Caller must download
    # Assume it's a local path
    return source


def _compute_bounds_4326(dataset) -> list[float]:
    """Compute EPSG:4326 bounds from a rasterio dataset."""
    src_crs = dataset.crs
    if src_crs is None:
        # No CRS — assume already in 4326
        b = dataset.bounds
        return [b.left, b.bottom, b.right, b.top]

    bounds = transform_bounds(src_crs, "EPSG:4326", *dataset.bounds)
    return list(bounds)  # [minLon, minLat, maxLon, maxLat]


def _bounds_to_geojson_polygon(bounds: list[float]) -> dict:
    """Convert [minLon, minLat, maxLon, maxLat] to a GeoJSON Polygon."""
    min_lon, min_lat, max_lon, max_lat = bounds
    return {
        "type": "Feature",
        "geometry": {
            "type": "Polygon",
            "coordinates": [
                [
                    [min_lon, min_lat],
                    [max_lon, min_lat],
                    [max_lon, max_lat],
                    [min_lon, max_lat],
                    [min_lon, min_lat],
                ]
            ],
        },
        "properties": {},
    }


# ─── RGB Processing ─────────────────────────────────────────────────────────


def _process_rgb(
    tif_path: str,
    bands: list[int],
    alpha: float,
    nodata_override: float | None,
) -> tuple[np.ndarray, Any]:
    """Read 3 bands from a GeoTIFF and produce an RGBA numpy array.

    Returns (rgba_array [H, W, 4] uint8, rasterio_dataset).
    """
    ds = rasterio.open(tif_path)

    if len(bands) != 3:
        raise ValueError(f"RGB mode requires exactly 3 bands, got {len(bands)}")

    for b in bands:
        if b < 1 or b > ds.count:
            raise ValueError(
                f"Band {b} out of range. File has {ds.count} band(s)."
            )

    # Read the three bands
    r = ds.read(bands[0]).astype(np.float64)
    g = ds.read(bands[1]).astype(np.float64)
    b_arr = ds.read(bands[2]).astype(np.float64)

    # Determine nodata mask
    nodata = nodata_override if nodata_override is not None else ds.nodata
    mask = np.zeros(r.shape, dtype=bool)
    if nodata is not None:
        mask |= r == nodata
        mask |= g == nodata
        mask |= b_arr == nodata
    # Also mask NaN
    mask |= np.isnan(r)
    mask |= np.isnan(g)
    mask |= np.isnan(b_arr)

    # Normalize each band to 0-255 using percentile-based histogram stretch
    # This prevents bright outliers (clouds, sun glint) from crushing the rest to black
    def normalize_band(arr, m):
        valid = arr[~m]
        if len(valid) == 0:
            return np.zeros_like(arr, dtype=np.uint8)
        # Use 2nd and 98th percentiles instead of min/max for robust stretching
        p_low = float(np.percentile(valid, 2))
        p_high = float(np.percentile(valid, 98))
        if p_high == p_low:
            p_high = p_low + 1.0
        normalized = (arr - p_low) / (p_high - p_low) * 255.0
        return np.clip(normalized, 0, 255).astype(np.uint8)

    r_u8 = normalize_band(r, mask)
    g_u8 = normalize_band(g, mask)
    b_u8 = normalize_band(b_arr, mask)

    # Alpha channel: fully opaque for valid pixels, transparent for nodata/NaN
    # (actual translucency is handled by OpenLayers layer opacity, not baked into PNG)
    a_u8 = np.full(r.shape, 255, dtype=np.uint8)
    a_u8[mask] = 0

    # Stack to RGBA
    rgba = np.stack([r_u8, g_u8, b_u8, a_u8], axis=-1)

    return rgba, ds


# ─── Single-Band Processing ─────────────────────────────────────────────────


def _process_singleband(
    tif_path: str,
    band: int,
    colormap_name: str,
    alpha: float,
    vmin: float | None,
    vmax: float | None,
    percentile_min: float | None,
    percentile_max: float | None,
    nodata_override: float | None,
) -> tuple[np.ndarray, Any]:
    """Read a single band and apply a colormap. Returns (rgba [H,W,4] uint8, dataset)."""
    ds = rasterio.open(tif_path)

    if band < 1 or band > ds.count:
        raise ValueError(f"Band {band} out of range. File has {ds.count} band(s).")

    data = ds.read(band).astype(np.float64)

    # Determine nodata mask
    nodata = nodata_override if nodata_override is not None else ds.nodata
    mask = np.isnan(data)
    if nodata is not None:
        mask |= data == nodata

    # Get valid data for statistics
    valid = data[~mask]
    if len(valid) == 0:
        # All nodata — return fully transparent
        rgba = np.zeros((*data.shape, 4), dtype=np.uint8)
        return rgba, ds

    # Determine range
    actual_min = vmin
    actual_max = vmax

    if percentile_min is not None and actual_min is None:
        actual_min = float(np.percentile(valid, percentile_min))
    if percentile_max is not None and actual_max is None:
        actual_max = float(np.percentile(valid, percentile_max))

    if actual_min is None:
        actual_min = float(np.nanmin(valid))
    if actual_max is None:
        actual_max = float(np.nanmax(valid))

    if actual_max == actual_min:
        actual_max = actual_min + 1.0

    # Normalize to [0, 1]
    normalized = (data - actual_min) / (actual_max - actual_min)
    normalized = np.clip(normalized, 0.0, 1.0)

    # Apply colormap
    try:
        cmap = colormaps[colormap_name]
    except KeyError:
        available = ", ".join(sorted(colormaps)[:20])
        raise ValueError(
            f"Unknown colormap '{colormap_name}'. Some options: {available}"
        )

    # cmap returns (R, G, B, A) as floats in [0, 1]
    colored = cmap(normalized)  # shape: (H, W, 4)
    rgba = (colored * 255).astype(np.uint8)

    # Alpha channel: fully opaque for valid pixels, transparent for nodata/NaN
    # (actual translucency is handled by OpenLayers layer opacity, not baked into PNG)
    rgba[:, :, 3] = np.where(mask, 0, 255)

    return rgba, ds


# ─── Public API ──────────────────────────────────────────────────────────────


async def process_geotiff_rgb(
    source: str,
    asset_id: str,
    bands: list[int] | None = None,
    alpha: float = 1.0,
    nodata: float | None = None,
    config=None,
) -> GeoTIFFResult | GeoTIFFError:
    """Process an RGB GeoTIFF and render to PNG.

    Args:
        source: URL (http/https), file:// URI, or local path
        asset_id: UUID for the asset (used for filename)
        bands: 3-element list of band indices (1-based). Default [1,2,3]
        alpha: Opacity 0.0-1.0
        nodata: Optional nodata value override
        config: AppConfig instance

    Returns:
        GeoTIFFResult on success, GeoTIFFError on failure
    """
    if bands is None:
        bands = [1, 2, 3]

    max_bytes = _get_max_file_size_bytes(config)
    file_dir = _get_file_dir(config)
    tif_path = None
    is_temp = False

    try:
        # Resolve source
        local_path = _resolve_source(source)
        if local_path is not None:
            # Local file
            if not os.path.exists(local_path):
                return GeoTIFFError(
                    error="file_not_found",
                    detail=f"File not found: {local_path}",
                )
            file_size = os.path.getsize(local_path)
            if file_size > max_bytes:
                return GeoTIFFError(
                    error="file_too_large",
                    detail=(
                        f"This GeoTIFF is {file_size / (1024*1024):.1f} MB, "
                        f"exceeding the {max_bytes / (1024*1024):.0f} MB limit "
                        f"for direct rendering. {_get_tile_server_message(config)}"
                    ),
                    file_size_mb=file_size / (1024 * 1024),
                )
            tif_path = local_path
        else:
            # Download from URL
            tif_path, size_mb = await _download_to_tempfile(source, max_bytes)
            is_temp = True

        # Process
        rgba, ds = _process_rgb(tif_path, bands, alpha, nodata)
        bounds = _compute_bounds_4326(ds)
        band_count = ds.count
        crs_str = str(ds.crs) if ds.crs else "unknown"
        ds.close()

        # Save PNG
        img = Image.fromarray(rgba, "RGBA")
        png_filename = f"{asset_id}.png"
        png_path = file_dir / png_filename
        img.save(str(png_path), "PNG")

        return GeoTIFFResult(
            png_path=str(png_path),
            bounds=bounds,
            width=rgba.shape[1],
            height=rgba.shape[0],
            crs=crs_str,
            band_count=band_count,
            image_url=f"/api/files/{png_filename}",
        )

    except ValueError as e:
        msg = str(e)
        if "limit" in msg.lower():
            return GeoTIFFError(
                error="file_too_large",
                detail=f"{msg} {_get_tile_server_message(config)}",
            )
        return GeoTIFFError(error="processing_error", detail=str(e))
    except Exception as e:
        logger.exception("GeoTIFF RGB processing failed")
        return GeoTIFFError(error="processing_error", detail=str(e))
    finally:
        if is_temp and tif_path and os.path.exists(tif_path):
            os.unlink(tif_path)


async def process_geotiff_singleband(
    source: str,
    asset_id: str,
    band: int = 1,
    colormap: str = "viridis",
    alpha: float = 1.0,
    vmin: float | None = None,
    vmax: float | None = None,
    percentile_min: float | None = None,
    percentile_max: float | None = None,
    nodata: float | None = None,
    config=None,
) -> GeoTIFFResult | GeoTIFFError:
    """Process a single-band GeoTIFF with colormap and render to PNG.

    Args:
        source: URL, file:// URI, or local path
        asset_id: UUID for the asset
        band: Band index (1-based). Default 1.
        colormap: Matplotlib colormap name. Default "viridis".
        alpha: Opacity 0.0-1.0
        vmin: Manual range minimum
        vmax: Manual range maximum
        percentile_min: Lower percentile threshold (e.g. 2 for 2nd percentile)
        percentile_max: Upper percentile threshold (e.g. 98 for 98th percentile)
        nodata: Optional nodata value override
        config: AppConfig instance

    Returns:
        GeoTIFFResult on success, GeoTIFFError on failure
    """
    max_bytes = _get_max_file_size_bytes(config)
    file_dir = _get_file_dir(config)
    tif_path = None
    is_temp = False

    try:
        local_path = _resolve_source(source)
        if local_path is not None:
            if not os.path.exists(local_path):
                return GeoTIFFError(
                    error="file_not_found",
                    detail=f"File not found: {local_path}",
                )
            file_size = os.path.getsize(local_path)
            if file_size > max_bytes:
                return GeoTIFFError(
                    error="file_too_large",
                    detail=(
                        f"This GeoTIFF is {file_size / (1024*1024):.1f} MB, "
                        f"exceeding the {max_bytes / (1024*1024):.0f} MB limit "
                        f"for direct rendering. {_get_tile_server_message(config)}"
                    ),
                    file_size_mb=file_size / (1024 * 1024),
                )
            tif_path = local_path
        else:
            tif_path, size_mb = await _download_to_tempfile(source, max_bytes)
            is_temp = True

        # Process
        rgba, ds = _process_singleband(
            tif_path, band, colormap, alpha,
            vmin, vmax, percentile_min, percentile_max, nodata,
        )
        bounds = _compute_bounds_4326(ds)
        band_count = ds.count
        crs_str = str(ds.crs) if ds.crs else "unknown"
        ds.close()

        # Save PNG
        img = Image.fromarray(rgba, "RGBA")
        png_filename = f"{asset_id}.png"
        png_path = file_dir / png_filename
        img.save(str(png_path), "PNG")

        return GeoTIFFResult(
            png_path=str(png_path),
            bounds=bounds,
            width=rgba.shape[1],
            height=rgba.shape[0],
            crs=crs_str,
            band_count=band_count,
            image_url=f"/api/files/{png_filename}",
        )

    except ValueError as e:
        msg = str(e)
        if "limit" in msg.lower():
            return GeoTIFFError(
                error="file_too_large",
                detail=f"{msg} {_get_tile_server_message(config)}",
            )
        return GeoTIFFError(error="processing_error", detail=str(e))
    except Exception as e:
        logger.exception("GeoTIFF single-band processing failed")
        return GeoTIFFError(error="processing_error", detail=str(e))
    finally:
        if is_temp and tif_path and os.path.exists(tif_path):
            os.unlink(tif_path)
